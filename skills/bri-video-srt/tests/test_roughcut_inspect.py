#!/usr/bin/env python3
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "roughcut_inspect.py"
SPEC = importlib.util.spec_from_file_location("roughcut_inspect", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RoughcutInspectTests(unittest.TestCase):
    def test_source_time_maps_to_output_timeline(self):
        keep = [{"start": 0, "end": 10}, {"start": 20, "end": 30}]
        self.assertEqual(MODULE.source_time_to_output(20, keep), 10)
        self.assertEqual(MODULE.source_time_to_output(25, keep), 15)

    def test_source_time_uses_rendered_segment_boundaries(self):
        keep = [
            {"start": 0, "end": 10, "output_start": 0, "output_end": 10.1},
            {"start": 20, "end": 30, "output_start": 10.1, "output_end": 20.2},
        ]
        self.assertEqual(MODULE.source_time_to_output(20, keep), 10.1)
        self.assertAlmostEqual(MODULE.source_time_to_output(25, keep), 15.1)

    def test_av_sync_metrics_include_start_and_end(self):
        probe = {"streams": [
            {"codec_type": "video", "start_time": "0", "duration": "10.000"},
            {"codec_type": "audio", "start_time": "0.010", "duration": "9.980"},
        ]}
        metrics = MODULE.av_sync_metrics(probe)
        self.assertAlmostEqual(metrics["start_delta_seconds"], 0.01)
        self.assertAlmostEqual(metrics["end_delta_seconds"], -0.01)
        self.assertAlmostEqual(metrics["max_absolute_delta_seconds"], 0.01)

    def test_sample_times_include_both_sides_of_each_repeat_cut(self):
        cutlist = {
            "kept_ranges_seconds": [{"start": 0, "end": 10}, {"start": 20, "end": 30}],
            "manual_repeat_ranges_seconds": [{"start": 20, "end": 20}],
        }
        times = MODULE.sample_times(20, 10, cutlist)
        self.assertIn(9.8, times)
        self.assertIn(10.2, times)

    def test_last_srt_end_is_parsed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.srt"
            path.write_text(
                "1\n00:00:01,000 --> 00:00:02,500\n测试\n", encoding="utf-8"
            )
            self.assertEqual(MODULE.last_srt_end(path), 2.5)


if __name__ == "__main__":
    unittest.main()
