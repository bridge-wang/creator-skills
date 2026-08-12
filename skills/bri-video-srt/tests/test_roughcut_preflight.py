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
    def test_resolve_prefers_longest_nearby_silence_end(self):
        candidate = [{"id": "take", "retain_hint": 20, "discard_start": 10,
                      "left_anchors": ["前句"], "right_anchors": ["后句"]}]
        result = MODULE.resolve_ranges(candidate, [[13, 16], [17, 19.8], [30, 40]])
        self.assertEqual(result[0]["discard_end"], 16)

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
