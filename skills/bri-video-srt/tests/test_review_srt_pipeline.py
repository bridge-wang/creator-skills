#!/usr/bin/env python3
import argparse
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "review_srt_pipeline.py"
SPEC = importlib.util.spec_from_file_location("review_srt_pipeline", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def arguments(directory, skip_auto=False):
    root = Path(directory)
    return argparse.Namespace(
        media=root / "media.mp4",
        raw_srt=root / "raw.srt",
        output_srt=root / "output.srt",
        draft_srt=root / "draft.srt",
        max_chars=19,
        threshold="-35dB",
        skip_auto=skip_auto,
    )


class ReviewSrtPipelineTests(unittest.TestCase):
    def test_draft_lint_runs_before_reanchor(self):
        with tempfile.TemporaryDirectory() as directory:
            commands = MODULE.build_commands(arguments(directory))
            self.assertIn("auto", commands[0])
            self.assertIn("lint", commands[1])
            self.assertIn("srt_audio_reanchor.py", commands[2][1])
            self.assertIn("--threshold=-35dB", commands[2])

    def test_lint_failure_stops_before_reanchor(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []

            def runner(command, check):
                calls.append(command)
                if "lint" in command:
                    raise subprocess.CalledProcessError(1, command)

            with self.assertRaises(subprocess.CalledProcessError):
                MODULE.run_pipeline(arguments(directory), runner=runner)
            self.assertEqual(len(calls), 2)
            self.assertNotIn("srt_audio_reanchor.py", " ".join(calls[-1]))

    def test_skip_auto_requires_existing_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SystemExit):
                MODULE.run_pipeline(arguments(directory, skip_auto=True), runner=lambda *_a, **_k: None)


if __name__ == "__main__":
    unittest.main()
