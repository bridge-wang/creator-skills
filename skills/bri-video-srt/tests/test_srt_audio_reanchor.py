#!/usr/bin/env python3
import runpy
import types
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "srt_audio_reanchor.py"
MODULE = runpy.run_path(str(SCRIPT))


class AudioReanchorTests(unittest.TestCase):
    def test_silence_detection_disables_video_decode(self):
        calls = []

        def runner(command, **_kwargs):
            calls.append(command)
            return types.SimpleNamespace(
                stderr=(
                    "[silencedetect] silence_start: 1.000\n"
                    "[silencedetect] silence_end: 1.500 | silence_duration: 0.500\n"
                )
            )

        with patch.object(MODULE["subprocess"], "run", side_effect=runner):
            intervals = MODULE["silence_intervals"](Path("video.mp4"), "-35dB", 0.12)

        self.assertEqual(intervals, [(1.0, 1.5)])
        self.assertEqual(len(calls), 1)
        self.assertIn("-vn", calls[0])
        self.assertLess(calls[0].index("-vn"), calls[0].index("-af"))


if __name__ == "__main__":
    unittest.main()
