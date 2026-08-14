#!/usr/bin/env python3
import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "autocut.py"
SPEC = importlib.util.spec_from_file_location("autocut", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AutocutTests(unittest.TestCase):
    def test_progress_ignores_ffmpeg_na_and_invalid_values(self):
        self.assertIsNone(MODULE.parse_progress_seconds("out_time_ms", "N/A"))
        self.assertIsNone(MODULE.parse_progress_seconds("out_time_us", "nan"))
        self.assertIsNone(MODULE.parse_progress_seconds("progress", "end"))

    def test_progress_uses_expected_output_duration(self):
        seconds = MODULE.parse_progress_seconds("out_time_ms", "281000000")
        self.assertEqual(MODULE.progress_percent(seconds, 562), 50)
        self.assertNotEqual(MODULE.progress_percent(seconds, 735), 50)

    def test_progress_reaches_99_only_until_process_exit(self):
        self.assertEqual(MODULE.progress_percent(600, 562), 99)

    def test_report_records_expected_output_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "cutlist.json"
            args = argparse.Namespace(max_pause=1.05, head_pad=0.17, tail_pad=0.37)
            MODULE.write_cutlist_report(
                target, "source.mp4", "output.mp4", 10.0, args,
                [(0.0, 4.0), (5.0, 9.0)], None,
            )
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["expected_output_duration_seconds"], 8.0)


if __name__ == "__main__":
    unittest.main()
