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
    def test_resolve_prefers_nearest_qualifying_silence_end(self):
        candidate = [{"id": "take", "retain_hint": 20, "discard_start": 10,
                      "left_anchors": ["前句"], "right_anchors": ["后句"]}]
        result = MODULE.resolve_ranges(candidate, [[13, 16], [17, 19.8], [30, 40]])
        self.assertEqual(result[0]["discard_end"], 19.8)
        self.assertEqual(result[0]["snap_selection"], "nearest_end_then_longest")

    def test_resolve_uses_longer_silence_only_when_distance_ties(self):
        candidate = [{"id": "take", "retain_hint": 20, "discard_start": 10,
                      "left_anchors": ["前句"], "right_anchors": ["后句"]}]
        result = MODULE.resolve_ranges(candidate, [[15, 19], [18, 21]])
        self.assertEqual(result[0]["discard_end"], 19)

    def test_third_lesson_regression_chooses_near_complete_retake(self):
        candidate = [{"id": "quality", "retain_hint": 445.72, "discard_start": 423.72,
                      "left_anchors": ["三手理解"], "right_anchors": ["分析到了这一步"]}]
        intervals = [[431.181875, 439.533313], [440.748688, 444.76225]]
        result = MODULE.resolve_ranges(candidate, intervals)
        self.assertEqual(result[0]["discard_end"], 444.76225)

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


if __name__ == "__main__":
    unittest.main()
