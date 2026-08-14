#!/usr/bin/env python3
"""一次性生成粗剪审核 SRT，并保证重锚定前草稿已通过长度 lint。

流程顺序固定为：自动校准/拆分 → 草稿 lint → 音频重锚定 → 中文排版 → 最终
lint。草稿 lint 失败时立即停止，因此不会为同一份超长字幕重复跑整片
silencedetect。人工修好草稿后可用 ``--skip-auto`` 从草稿 lint 继续。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
CALIBRATE = SCRIPT_DIR / "srt_calibrate.py"
REANCHOR = SCRIPT_DIR / "srt_audio_reanchor.py"
TYPOGRAPHY = SCRIPT_DIR / "zh_typography.py"


def build_commands(args):
    commands = []
    if not args.skip_auto:
        commands.append([
            sys.executable, str(CALIBRATE), "auto", str(args.raw_srt),
            "--output", str(args.draft_srt),
            "--no-preserve-timing", "--max-chars", str(args.max_chars),
        ])
    commands.extend([
        [sys.executable, str(CALIBRATE), "lint", str(args.draft_srt),
         "--max-chars", str(args.max_chars)],
        [sys.executable, str(REANCHOR), str(args.media), str(args.draft_srt),
         str(args.output_srt), f"--threshold={args.threshold}"],
        [sys.executable, str(TYPOGRAPHY), str(args.output_srt)],
        [sys.executable, str(CALIBRATE), "lint", str(args.output_srt),
         "--max-chars", str(args.max_chars)],
    ])
    return commands


def run_pipeline(args, runner=subprocess.run):
    if args.skip_auto and not args.draft_srt.is_file():
        raise SystemExit(f"--skip-auto 需要现有草稿：{args.draft_srt}")
    for command in build_commands(args):
        runner(command, check=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("media", type=Path)
    parser.add_argument("raw_srt", type=Path)
    parser.add_argument("output_srt", type=Path)
    parser.add_argument("--draft-srt", type=Path, required=True)
    parser.add_argument("--max-chars", type=int, default=19)
    parser.add_argument("--threshold", default="-35dB")
    parser.add_argument("--skip-auto", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    run_pipeline(args)
    print(f"审核字幕 → {args.output_srt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
