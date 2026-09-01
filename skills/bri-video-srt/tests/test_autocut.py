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
    def test_padding_uses_the_full_requested_pause(self):
        padded = MODULE.add_benchmark_padding(
            [(1.0, 2.0), (4.0, 5.0)], 6.0, 1.05, 0.17, 0.37,
        )
        retained_pause = (padded[0][1] - 2.0) + (4.0 - padded[1][0])
        self.assertAlmostEqual(retained_pause, 1.05)

    def test_repeat_ranges_are_not_reintroduced_by_generic_padding(self):
        padded = MODULE.add_benchmark_padding(
            [(0.0, 2.0), (8.0, 10.0)], 10.0, 1.05, 0.17, 0.37,
        )
        final = MODULE.subtract_ranges_from_keep(padded, [(2.0, 8.0)])
        self.assertEqual(final, [(0.0, 2.0), (8.0, 10.0)])

    def test_emphasis_pause_ranges_are_merged_after_repeat_cut(self):
        keep = [(0.0, 2.0), (8.0, 10.0)]
        restored = MODULE.merge_intervals(keep + [(7.6, 8.0)])
        self.assertEqual(restored, [(0.0, 2.0), (7.6, 10.0)])

    def test_emphasis_plan_requires_audited_sentence_and_ranges(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            path.write_text(json.dumps({"audit_pass": False}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                MODULE.load_emphasis_pause_plan(path)

    def test_progress_ignores_ffmpeg_na_and_invalid_values(self):
        self.assertIsNone(MODULE.parse_progress_seconds("out_time_ms", "N/A"))
        self.assertIsNone(MODULE.parse_progress_seconds("out_time_us", "nan"))
        self.assertIsNone(MODULE.parse_progress_seconds("progress", "end"))

    def test_reuse_report_is_explicit(self):
        args = MODULE.parse_args(["source.mp4", "old.json", "new.mp4", "--reuse-report"])
        self.assertTrue(args.reuse_report)

    def test_plan_only_requires_report(self):
        with self.assertRaises(SystemExit):
            MODULE.parse_args(["source.mp4", "cuts.json", "new.mp4", "--plan-only"])
        args = MODULE.parse_args([
            "source.mp4", "cuts.json", "new.mp4", "--plan-only", "--report", "plan.json",
        ])
        self.assertTrue(args.plan_only)

    def test_protected_ranges_must_reach_the_final_cutlist(self):
        payload = {"chunks": [[0, 60, 1.0], [60, 180, 99999.0], [180, 240, 1.0]]}
        audit = {"protected_ranges": [{"id": "wait", "start": 1, "end": 3}]}
        with self.assertRaises(SystemExit):
            MODULE.verify_protected_ranges(payload, 60, audit)
        protected = {"chunks": [[0, 240, 1.0]]}
        MODULE.verify_protected_ranges(protected, 60, audit)
        reused_report = {"kept_ranges_seconds": [{"start": 0, "end": 4}]}
        MODULE.verify_protected_ranges(reused_report, 60, audit)

    def test_capped_protected_range_only_requires_recorded_edges(self):
        payload = {"chunks": [
            [0, 270, 1.0], [270, 1050, 99999.0], [1050, 1320, 1.0],
        ]}
        audit = {"protected_ranges": [{
            "id": "wait", "start": 1, "end": 21,
            "retained_ranges_seconds": [
                {"start": 1, "end": 4.5}, {"start": 17.5, "end": 21},
            ],
        }]}
        MODULE.verify_protected_ranges(payload, 60, audit)

    def test_validated_repeat_can_override_protected_range(self):
        payload = {"chunks": [[0, 120, 1.0], [120, 180, 99999.0], [180, 240, 1.0]]}
        audit = {"protected_ranges": [{"id": "retake", "start": 1, "end": 3}]}
        preflight = {"resolved_ranges": [{"discard_start": 2, "discard_end": 3}]}
        MODULE.verify_protected_ranges(payload, 60, audit, preflight=preflight)

    def test_unvalidated_gap_inside_protected_range_still_fails(self):
        payload = {"chunks": [[0, 120, 1.0], [120, 180, 99999.0], [180, 240, 1.0]]}
        audit = {"protected_ranges": [{"id": "retake", "start": 1, "end": 3}]}
        with self.assertRaises(SystemExit):
            MODULE.verify_protected_ranges(payload, 60, audit, preflight={})

    def test_progress_uses_expected_output_duration(self):
        seconds = MODULE.parse_progress_seconds("out_time_ms", "281000000")
        self.assertEqual(MODULE.progress_percent(seconds, 562), 50)
        self.assertNotEqual(MODULE.progress_percent(seconds, 735), 50)

    def test_progress_reaches_99_only_until_process_exit(self):
        self.assertEqual(MODULE.progress_percent(600, 562), 99)

    def test_mux_duration_is_limited_to_expected_last_frame(self):
        self.assertEqual(MODULE.mux_duration_args(724.65), ["-t", "724.650000000"])

    def test_report_records_expected_output_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "cutlist.json"
            args = argparse.Namespace(max_pause=1.05, head_pad=0.17, tail_pad=0.37)
            ranges = MODULE.quantize_keep_ranges([(0.0, 4.0), (5.0, 9.0)], 30)
            MODULE.write_cutlist_report(
                target, "source.mp4", "output.mp4", 10.0, args,
                ranges, None, requested_duration=8.0,
            )
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["expected_output_duration_seconds"], 8.0)
            self.assertEqual(payload["render_strategy"], "paired_segment_concat_v1")
            self.assertEqual(payload["protected_operation_ranges_seconds"], [])

    def test_cut_boundaries_are_quantized_once_for_both_streams(self):
        ranges = MODULE.quantize_keep_ranges([(0.011, 1.011), (2.019, 3.019)], 60)
        self.assertEqual(ranges[0]["start_frame"], 1)
        self.assertEqual(ranges[0]["end_frame"], 61)
        self.assertAlmostEqual(ranges[1]["output_start"], 1.0)

    def test_filter_graph_pairs_trimmed_video_and_audio_before_concat(self):
        ranges = MODULE.quantize_keep_ranges([(0.011, 1.011), (2.019, 3.019)], 60)
        graph = MODULE.build_paired_concat_filter(ranges)
        self.assertIn("trim=start_frame=1:end_frame=61", graph)
        self.assertIn("atrim=start=0.016666667:end=1.016666667", graph)
        self.assertIn("concat=n=2:v=1:a=1[vout][aout]", graph)
        self.assertNotIn("select=", graph)
        self.assertNotIn("aselect=", graph)


if __name__ == "__main__":
    unittest.main()
