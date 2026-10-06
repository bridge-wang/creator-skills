#!/usr/bin/env python3
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
import wave
import array
from pathlib import Path


AUTOCUT_SCRIPT = Path(__file__).parents[1] / "scripts" / "autocut.py"
INSPECT_SCRIPT = Path(__file__).parents[1] / "scripts" / "roughcut_inspect.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUTOCUT = load_module("autocut_integration", AUTOCUT_SCRIPT)
INSPECT = load_module("roughcut_inspect_integration", INSPECT_SCRIPT)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 ffmpeg")
class AutocutIntegrationTests(unittest.TestCase):
    def test_vfr_and_delayed_audio_preserve_source_timeline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "vfr.mp4", root / "cut.mp4"
            subprocess.run([
                "ffmpeg", "-nostdin", "-v", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=6",
                "-itsoffset", "0.155", "-f", "lavfi", "-i",
                "aevalsrc=if(between(t\\,4\\,4.1)\\,0.5*sin(2*PI*1000*t)\\,0):s=48000:d=6",
                "-vf", "select=not(eq(mod(n+1\\,10)\\,0))", "-fps_mode", "vfr",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(source),
            ], check=True)
            ranges = AUTOCUT.quantize_keep_ranges([(4.0, 5.0)], 30)
            subprocess.run([
                "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source),
                "-filter_complex", AUTOCUT.build_paired_concat_filter(ranges, "30"),
                "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264",
                "-preset", "ultrafast", "-c:a", "aac", str(output),
            ], check=True)
            self.assertAlmostEqual(float(INSPECT.probe_media(output)["format"]["duration"]), 1.0, delta=.05)
            wav_path = root / "cut.wav"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(output),
                            "-vn", "-ac", "1", "-c:a", "pcm_s16le", str(wav_path)], check=True)
            with wave.open(str(wav_path)) as reader:
                rate = reader.getframerate()
                samples = array.array("h", reader.readframes(reader.getnframes()))
            onset = next(i for i, sample in enumerate(samples) if abs(sample) > 5000) / rate
            self.assertAlmostEqual(onset, .155, delta=.025)

    def test_many_segments_do_not_accumulate_audio_video_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            output = root / "output.mp4"
            subprocess.run([
                "ffmpeg", "-nostdin", "-v", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=15",
                "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=15",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", str(source),
            ], check=True)

            requested = [(index * 0.5 + 0.011, index * 0.5 + 0.344)
                         for index in range(30)]
            ranges = AUTOCUT.quantize_keep_ranges(requested, 30)
            graph = AUTOCUT.build_paired_concat_filter(ranges)
            subprocess.run([
                "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source),
                "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", str(output),
            ], check=True)

            metrics = INSPECT.av_sync_metrics(INSPECT.probe_media(output))
            self.assertLessEqual(metrics["max_absolute_delta_seconds"], 0.05)
            self.assertLessEqual(abs(metrics["end_delta_seconds"]), 0.05)


if __name__ == "__main__":
    unittest.main()
