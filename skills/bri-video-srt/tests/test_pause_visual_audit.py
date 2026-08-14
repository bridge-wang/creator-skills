#!/usr/bin/env python3
import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "pause_visual_audit.py"
SPEC = importlib.util.spec_from_file_location("pause_visual_audit", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PauseVisualAuditTests(unittest.TestCase):
    def test_candidates_include_semantic_operation_signal(self):
        payload = {"chunks": [[0, 60, 1.0], [60, 180, 99999.0], [180, 240, 1.0]]}
        cues = [(1.5, 2.5, "我们把链接粘贴过来"), (3, 4, "等待模型回答")]
        result = MODULE.build_candidates(payload, 60, cues, 1.5)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["semantic_operation_signal"])
        self.assertEqual(
            result[0]["sample_labels"],
            ["context_before", "pause_start", "pause_middle", "pause_end", "context_after"],
        )
        self.assertEqual(result[0]["sample_times"], [0.8, 1.1, 2.0, 2.9, 3.2])
        self.assertTrue(result[0]["contains_asr_midpoint"])
        self.assertTrue(result[0]["possible_quiet_speech"])

    def test_short_pause_is_not_forced_into_visual_review(self):
        payload = {"chunks": [[0, 60, 1.0], [60, 120, 99999.0], [120, 180, 1.0]]}
        self.assertEqual(MODULE.build_candidates(payload, 60, [], 1.5), [])

    def test_context_after_never_points_past_the_last_real_video_frame(self):
        payload = {"chunks": [[0, 60, 1.0], [60, 180, 99999.0]]}
        result = MODULE.build_candidates(payload, 60, [], 1.5)
        self.assertEqual(result[0]["sample_times"][-1], 2.983333)

    def test_short_protected_pause_is_restored_and_ordinary_pause_stays_cut(self):
        payload = {"chunks": [
            [0, 60, 1.0], [60, 180, 99999.0], [180, 240, 1.0],
            [240, 360, 99999.0], [360, 420, 1.0],
        ]}
        candidates = [
            {
                "start": 1, "end": 3, "classification": "wait_generation",
                "protected_ranges_seconds": [{"start": 1, "end": 3}],
            },
            {"start": 4, "end": 6, "classification": "ordinary_speech_pause"},
        ]
        result = MODULE.apply_protected_ranges(payload, 60, candidates)
        self.assertEqual(result["chunks"], [[0, 240, 1.0], [240, 360, 99999.0], [360, 420, 1.0]])

    def test_long_protected_blank_keeps_balanced_seven_seconds(self):
        payload = {"chunks": [[0, 60, 1.0], [60, 1260, 99999.0], [1260, 1320, 1.0]]}
        candidates = [{
            "start": 1, "end": 21, "duration": 20,
            "classification": "wait_generation", "possible_quiet_speech": False,
        }]
        result = MODULE.apply_protected_ranges(payload, 60, candidates)
        self.assertEqual(result["chunks"], [
            [0, 270, 1.0], [270, 1050, 99999.0], [1050, 1320, 1.0],
        ])
        retained = MODULE.protected_retention_frames(candidates[0], 60, 7.0)
        self.assertEqual(retained, [(60, 270), (1050, 1260)])

    def test_possible_quiet_speech_bypasses_seven_second_cap(self):
        candidate = {
            "start": 1, "end": 21, "duration": 20,
            "classification": "retake_context", "possible_quiet_speech": True,
        }
        self.assertEqual(
            MODULE.protected_retention_frames(candidate, 60, 7.0), [(60, 1260)]
        )

    def test_uncertain_and_retake_default_to_compress(self):
        self.assertNotIn("uncertain", MODULE.PROTECTED_CLASSES)
        self.assertNotIn("retake_context", MODULE.PROTECTED_CLASSES)
        self.assertIn("uncertain", MODULE.COMPRESS_CLASSES)
        self.assertIn("retake_context", MODULE.COMPRESS_CLASSES)
        self.assertIn("quiet_speech", MODULE.PROTECTED_CLASSES)

    def test_missing_classification_or_reason_fails_closed(self):
        with self.assertRaises(SystemExit):
            MODULE.validate_classifications([{"id": "pause_001", "classification": None, "reason": ""}])

    def test_separate_decisions_are_merged_by_id(self):
        candidates = [{"id": "pause_001", "classification": None, "reason": ""}]
        result = MODULE.merge_decisions(candidates, {"decisions": [{
            "id": "pause_001", "classification": "operation", "reason": "正在切换网页",
            "visual_evidence": "1.2 秒为课件，1.8 秒为模型页面",
            "protected_ranges_seconds": [{"start": 1.2, "end": 1.8}],
        }]})
        self.assertEqual(result[0]["classification"], "operation")
        self.assertEqual(result[0]["reason"], "正在切换网页")
        self.assertEqual(result[0]["visual_evidence"], "1.2 秒为课件，1.8 秒为模型页面")
        self.assertEqual(result[0]["protected_ranges_seconds"], [{"start": 1.2, "end": 1.8}])

    def test_protected_class_requires_positive_visual_evidence_and_minimum_window(self):
        candidate = {
            "id": "pause_001", "start": 1, "end": 4,
            "classification": "page_switch", "reason": "切换页面",
            "possible_quiet_speech": False,
        }
        with self.assertRaises(SystemExit):
            MODULE.validate_classifications([candidate])

        candidate.update({
            "visual_evidence": "2.0 秒还是课件，2.4 秒已进入模型页",
            "protected_ranges_seconds": [{"start": 2.0, "end": 2.4}],
        })
        MODULE.validate_classifications([candidate])

    def test_only_explicit_operation_window_is_restored(self):
        payload = {"chunks": [[0, 600, 99999.0]]}
        candidate = {
            "start": 1, "end": 9, "duration": 8,
            "classification": "page_switch", "possible_quiet_speech": False,
            "protected_ranges_seconds": [{"start": 4.2, "end": 4.8}],
        }
        result = MODULE.apply_protected_ranges(payload, 60, [candidate])
        self.assertEqual(result["chunks"], [
            [0, 252, 99999.0], [252, 288, 1.0], [288, 600, 99999.0],
        ])

    def test_asr_speech_inside_low_volume_range_cannot_be_called_ordinary_pause(self):
        with self.assertRaises(SystemExit):
            MODULE.validate_classifications([{
                "id": "pause_001", "classification": "ordinary_speech_pause",
                "reason": "误判", "possible_quiet_speech": True,
            }])

    def test_confirmed_silence_suppresses_false_quiet_speech_alarm(self):
        payload = {"chunks": [[0, 60, 1.0], [60, 180, 99999.0], [180, 240, 1.0]]}
        cues = [(1.5, 2.5, "Whisper 的宽时间戳")]
        result = MODULE.build_candidates(payload, 60, cues, 1.5, silences=[(1, 3)])
        self.assertEqual(result[0]["confirmed_silence_ratio"], 1.0)
        self.assertFalse(result[0]["possible_quiet_speech"])


if __name__ == "__main__":
    unittest.main()
