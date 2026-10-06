#!/usr/bin/env python3
import importlib.util
import json
import tempfile
import unittest
import wave
import argparse
import contextlib
import io
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "roughcut_preflight.py"
SPEC = importlib.util.spec_from_file_location("roughcut_preflight", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RoughcutPreflightTests(unittest.TestCase):
    def test_overlapping_preview_context_is_not_a_cross_sample_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            srt = root / 'preview.srt'
            srt.write_text('1\n00:00:00,000 --> 00:00:02,000\n完整语义锚点\n\n'
                           '2\n00:00:02,800 --> 00:00:04,800\n完整语义锚点\n', encoding='utf-8')
            report = root / 'report.json'
            report.write_text(json.dumps({'preview_mapping': [
                {'id': 'a', 'preview_start': 0, 'preview_end': 2,
                 'left_anchors': ['完整语义'], 'right_anchors': ['锚点']},
                {'id': 'b', 'preview_start': 2.8, 'preview_end': 4.8,
                 'left_anchors': ['完整语义'], 'right_anchors': ['锚点']},
            ]}), encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                MODULE.validate_preview(argparse.Namespace(preview_srt=srt, report=report))
            self.assertTrue(json.loads(report.read_text())['preflight_pass'])

    def test_silence_exemption_does_not_hide_two_long_unprotected_edges(self):
        cutlist = {'kept_ranges_seconds': [{'start': 0, 'end': 2, 'output_start': 0, 'output_end': 2}],
                   'emphasis_pause_ranges_seconds': [{'retained_ranges_seconds': [{'start': .4, 'end': 1.6}]}]}
        issues, _ = MODULE.unexplained_long_silences([[0, 2]], cutlist, .308, .04)
        self.assertEqual(len(issues), 1)

    def test_silence_merge_does_not_swallow_short_low_volume_sentence_start(self):
        events = [[10.0, 12.0], [12.22, 14.0]]
        self.assertEqual(MODULE.merge_silences(events), events)

    def test_silence_merge_still_repairs_detector_micro_gaps(self):
        self.assertEqual(MODULE.merge_silences([[10.0, 12.0], [12.04, 14.0]]),
                         [[10.0, 14.0]])

    def test_resolve_prefers_nearest_qualifying_silence_end(self):
        candidate = [{"id": "take", "retain_hint": 20, "discard_start": 10,
                      "left_anchors": ["前句"], "right_anchors": ["后句"]}]
        result = MODULE.resolve_ranges(candidate, [[13, 16], [17, 19.8], [30, 40]])
        self.assertEqual(result[0]["detected_silence_end"], 19.8)
        self.assertEqual(result[0]["discard_end"], 19.34)
        self.assertEqual(result[0]["snap_selection"], "nearest_end_then_longest")

    def test_resolve_uses_longer_silence_only_when_distance_ties(self):
        candidate = [{"id": "take", "retain_hint": 20, "discard_start": 10,
                      "left_anchors": ["前句"], "right_anchors": ["后句"]}]
        result = MODULE.resolve_ranges(candidate, [[15, 19], [18, 21]])
        self.assertEqual(result[0]["detected_silence_end"], 19)

    def test_third_lesson_regression_chooses_near_complete_retake(self):
        candidate = [{"id": "quality", "retain_hint": 445.72, "discard_start": 423.72,
                      "left_anchors": ["三手理解"], "right_anchors": ["分析到了这一步"]}]
        intervals = [[431.181875, 439.533313], [440.748688, 444.76225]]
        result = MODULE.resolve_ranges(candidate, intervals)
        self.assertEqual(result[0]["detected_silence_end"], 444.76225)

    def test_apply_ranges_turns_only_overlapping_keep_frames_into_cut(self):
        payload = {"chunks": [[0, 100, 1.0], [100, 120, 99999.0], [120, 200, 1.0]]}
        resolved = [{"discard_start": 0.5, "discard_end": 1.5}]
        output = MODULE.apply_ranges(payload, 100, resolved)
        self.assertEqual(output["chunks"], [[0, 50, 1.0], [50, 150, 99999.0], [150, 200, 1.0]])

    def test_preview_defaults_to_ten_seconds_left_context(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            target = Path(directory) / "preview.wav"
            with wave.open(str(source), "wb") as writer:
                writer.setparams((1, 2, 100, 0, "NONE", "not compressed"))
                writer.writeframes(b"\0\0" * 3000)
            mapping = MODULE.write_preview(source, target, [{
                "id": "take", "discard_start": 20, "discard_end": 22,
                "left_anchors": ["前句"], "right_anchors": ["后句"],
            }])
            self.assertAlmostEqual(mapping[0]["preview_end"], 15.0, places=2)

    def test_validation_accepts_asr_anchor_variant(self):
        text = MODULE.normalized("在两天框里面就可以交互，但这里也有一个例外")
        self.assertEqual(MODULE.anchors_in_text(text, ["在聊天框里面就可以交互", "就可以交互"]),
                         ["就可以交互"])

    def test_phrase_count_catches_retained_duplicate(self):
        text = MODULE.normalized("要提高 Agent 的稳定性，要提高 Agent 的稳定性")
        result = MODULE.phrase_count_results(text, [{
            "anchors": ["要提高 Agent 的稳定性"], "min": 1, "max": 1,
        }])
        self.assertFalse(result[0]["pass"])
        self.assertTrue(MODULE.find_tandem_repeats(text))

    def test_full_semantic_contract_requires_complete_bridge_and_retake_prefix(self):
        bad = MODULE.normalized("这里我有几个自己的故事跟大家说是我今年七月份确诊")
        item = {
            "left_anchors": ["跟大家分享一下"],
            "right_anchors": ["第一个是我在今年的七月份"],
        }
        left, right, _, ok = MODULE.validate_candidate_text(bad, item)
        self.assertFalse(left)
        self.assertFalse(right)
        self.assertTrue(ok)

    def test_write_kept_preview_uses_final_ranges(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            target = Path(directory) / "preview.wav"
            with wave.open(str(source), "wb") as writer:
                writer.setparams((1, 2, 100, 0, "NONE", "not compressed"))
                writer.writeframes(b"\1\0" * 100 + b"\2\0" * 100)
            MODULE.write_kept_preview(source, target, {
                "kept_ranges_seconds": [{"start": 0, "end": 0.5}, {"start": 1, "end": 1.5}],
            })
            with wave.open(str(target), "rb") as reader:
                self.assertEqual(reader.getnframes(), 100)

    def test_long_silence_gate_rejects_asr_only_quiet_speech_regression(self):
        cutlist = {
            "kept_ranges_seconds": [{
                "start": 316.65, "end": 371.15,
                "output_start": 226.5, "output_end": 281.0,
            }],
            "protected_operation_ranges_seconds": [{
                "classification": "quiet_speech",
                "start": 328.216667, "end": 342.516667,
                "inside_text": ["首先选 AI 工具而不是 AI 模型"],
                "retained_ranges_seconds": [{"start": 328.216667, "end": 342.516667}],
            }],
        }
        issues, _ = MODULE.unexplained_long_silences(
            [[238.103271, 252.364458]], cutlist, 1.05,
        )
        self.assertEqual(len(issues), 1)
        self.assertAlmostEqual(issues[0]["silence_duration"], 14.261187, places=5)

    def test_long_silence_gate_accepts_positive_visual_operation_window(self):
        cutlist = {
            "kept_ranges_seconds": [{
                "start": 10, "end": 20, "output_start": 0, "output_end": 10,
            }],
            "protected_operation_ranges_seconds": [{
                "classification": "page_switch",
                "visual_evidence": "12.0 秒为旧页，12.8 秒已切到新页",
                "protected_ranges_seconds": [{"start": 12, "end": 12.8}],
            }],
        }
        issues, allowed = MODULE.unexplained_long_silences([[2, 2.8]], cutlist, 1.05)
        self.assertEqual(issues, [])
        self.assertEqual(len(allowed), 1)
        self.assertAlmostEqual(allowed[0][0], 2.0)
        self.assertAlmostEqual(allowed[0][1], 2.8)

    def test_long_silence_gate_accepts_only_verified_quiet_speech_range(self):
        cutlist = {
            "kept_ranges_seconds": [{
                "start": 10, "end": 20, "output_start": 0, "output_end": 10,
            }],
            "protected_operation_ranges_seconds": [{
                "classification": "quiet_speech",
                "audible_speech_evidence": "局部听审可听到完整句首",
                "verified_audio_ranges_seconds": [{"start": 12, "end": 14}],
            }],
        }
        issues, allowed = MODULE.unexplained_long_silences([[2, 4]], cutlist, 1.05)
        self.assertEqual(issues, [])
        self.assertEqual(allowed, [[2.0, 4.0]])

    def test_retake_context_must_map_to_real_repeat_candidate(self):
        audit = {"candidates": [{
            "id": "pause_009", "classification": "retake_context",
            "repeat_candidate_id": "intra_cue_restart",
        }]}
        with self.assertRaises(SystemExit):
            MODULE.validate_retake_coverage(audit, {"candidates": []})
        MODULE.validate_retake_coverage(audit, {
            "candidates": [{"id": "intra_cue_restart"}],
        })

    def test_silence_gate_independently_rejects_overlong_page_switch_metadata(self):
        cutlist = {
            "kept_ranges_seconds": [{
                "start": 0, "end": 10, "output_start": 0, "output_end": 10,
            }],
            "protected_operation_ranges_seconds": [{
                "id": "page_switch", "classification": "page_switch",
                "visual_evidence": "2 秒旧页，5 秒新页",
                "protected_ranges_seconds": [{"start": 2, "end": 5}],
            }],
        }
        with self.assertRaises(SystemExit):
            MODULE.allowed_long_silence_ranges(cutlist)

    def test_lesson1_intra_cue_restart_phrase_count_regression(self):
        text = MODULE.normalized(
            "所以说会编程才是程序员的瓶颈和壁垒"
            "但在现在我们反而要把但在现在我们反而要把功夫下在"
        )
        results = MODULE.phrase_count_results(text, [{
            "anchors": ["但在现在我们反而要把"], "min": 1, "max": 1,
        }])
        self.assertFalse(results[0]["pass"])

    def test_lesson1_identity_and_wrong_word_restarts_regression(self):
        bad_text = MODULE.normalized(
            "那当大家把这一个小时的视频学习完成之后"
            "当大家把这一个小时的课程学习下来之后"
            "也不过是在沙滩上建城堡在错误的在脆弱的地基上雕刻能力"
        )
        checks = MODULE.phrase_count_results(bad_text, [
            {"anchors": ["那当大家把这一个小时"], "min": 0, "max": 0},
            {"anchors": ["当大家把这一个小时的视频学习完成之后"], "min": 0, "max": 0},
            {"anchors": ["在错误的在脆弱的"], "min": 0, "max": 0},
        ])
        self.assertTrue(all(not result["pass"] for result in checks))

    def test_lesson1_ai_commander_false_bridge_regression(self):
        bad_text = MODULE.normalized(
            "AI味十足的结果AI指挥者会这么说而AI指挥者会截取对标网站"
        )
        result = MODULE.phrase_count_results(bad_text, [{
            "anchors": ["AI指挥者会这么说"], "min": 0, "max": 0,
        }])[0]
        self.assertFalse(result["pass"])


if __name__ == "__main__":
    unittest.main()
