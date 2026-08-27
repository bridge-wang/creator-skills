#!/usr/bin/env python3
"""用真实音频静音边界重锚定 SRT 句首，修复 Whisper 句段时间提前。

Whisper 的 SRT 常把下一句开始标在上一句结束处，即使中间还有静音。本脚本
只在字幕句首落入已验证的静音区间时，推迟该句到下一次真实开口，同时把前一句
收至静音开始处。它不猜测连续语音中的逐词边界。
"""
from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)
SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9.]+)")
SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.]+)")


@dataclass
class Cue:
    start: float
    end: float
    text: str


def parse_time(values: tuple[str, ...]) -> float:
    hour, minute, second, millis = map(int, values)
    return hour * 3600 + minute * 60 + second + millis / 1000


def format_time(value: float) -> str:
    total_ms = max(0, round(value * 1000))
    hour, remainder = divmod(total_ms, 3_600_000)
    minute, remainder = divmod(remainder, 60_000)
    second, millis = divmod(remainder, 1_000)
    return f"{hour:02}:{minute:02}:{second:02},{millis:03}"


def read_srt(path: Path) -> list[Cue]:
    raw = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        time_index = next((i for i, line in enumerate(lines) if TIME_RE.search(line)), None)
        if time_index is None:
            continue
        match = TIME_RE.search(lines[time_index])
        assert match is not None
        cues.append(
            Cue(
                parse_time(match.groups()[:4]),
                parse_time(match.groups()[4:]),
                " ".join(lines[time_index + 1 :]),
            )
        )
    if not cues:
        raise ValueError(f"No SRT cues found: {path}")
    return cues


def write_srt(cues: list[Cue], path: Path) -> None:
    blocks = []
    for index, cue in enumerate(cues, 1):
        blocks.append(
            f"{index}\n{format_time(cue.start)} --> {format_time(cue.end)}\n{cue.text}"
        )
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def silence_intervals(media: Path, threshold: str, minimum_duration: float) -> list[tuple[float, float]]:
    result = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-i", str(media),
            "-vn",
            "-af", f"silencedetect=n={threshold}:d={minimum_duration}",
            "-f", "null", "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    pending_starts: list[float] = []
    intervals: list[tuple[float, float]] = []
    for line in result.stderr.splitlines():
        start = SILENCE_START_RE.search(line)
        end = SILENCE_END_RE.search(line)
        if start:
            pending_starts.append(float(start.group(1)))
        elif end and pending_starts:
            intervals.append((pending_starts.pop(0), float(end.group(1))))

    # Breath/click artifacts can split one pause into intervals a few
    # milliseconds apart. They are not meaningful subtitle starts.
    merged: list[tuple[float, float]] = []
    for start, end in intervals:
        if merged and start - merged[-1][1] <= 0.08:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def reanchor(
    cues: list[Cue],
    silences: list[tuple[float, float]],
    lead_tolerance: float,
    minimum_shift: float,
) -> list[tuple[int, float, float]]:
    moved: list[tuple[int, float, float]] = []
    for index in range(1, len(cues)):
        cue = cues[index]
        for silence_start, silence_end in silences:
            # A raw Whisper boundary at the beginning of a following pause is
            # early. A boundary near the end is already a useful anchor.
            if not (silence_start - lead_tolerance <= cue.start <= silence_end - minimum_shift):
                continue
            if silence_end >= cue.end - 0.12:
                break
            old_start = cue.start
            cue.start = silence_end
            previous = cues[index - 1]
            if silence_start > previous.start + 0.12:
                previous.end = min(previous.end, silence_start)
            moved.append((index + 1, old_start, cue.start))
            break
    return moved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("media", type=Path, help="与 SRT 同时间轴的 MP4、MOV 或音频文件")
    parser.add_argument("input_srt", type=Path)
    parser.add_argument("output_srt", type=Path)
    parser.add_argument("--threshold", default="-35dB", help="ffmpeg silencedetect 阈值")
    parser.add_argument("--min-silence", type=float, default=0.12)
    parser.add_argument("--lead-tolerance", type=float, default=0.18)
    parser.add_argument("--minimum-shift", type=float, default=0.12)
    args = parser.parse_args()

    cues = read_srt(args.input_srt)
    silences = silence_intervals(args.media, args.threshold, args.min_silence)
    moved = reanchor(cues, silences, args.lead_tolerance, args.minimum_shift)
    write_srt(cues, args.output_srt)

    print(f"silence intervals: {len(silences)}")
    print(f"re-anchored cue starts: {len(moved)}")
    if moved:
        shifts = [new - old for _, old, new in moved]
        print(f"mean shift: {sum(shifts) / len(shifts):.3f}s; max shift: {max(shifts):.3f}s")


if __name__ == "__main__":
    main()
