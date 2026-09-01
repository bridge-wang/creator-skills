#!/usr/bin/env python3
import importlib.util
import json
import tempfile
import unittest
import wave
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "roughcut_preflight.py"
SPEC = importlib.util.spec_from_file_location("roughcut_preflight", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RoughcutPreflightTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
