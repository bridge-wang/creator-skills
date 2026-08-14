#!/usr/bin/env python3
"""一次解码完成粗剪媒体校验、代表帧和全部重复切点前后截图。

脚本对比源片与粗剪成片的关键视频属性，核对预计/实际时长和可选 SRT 末尾，
再用同一个 ffmpeg 进程完整解码音视频，并从同一解码流生成联系表。联系表与 JSON
报告应放在会话临时目录，不属于阶段 A 的三份正式交付物。除静态属性外，脚本
还强制检查音视频流的首尾差，防止“完整解码成功但口型逐段漂移”的假通过。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from pathlib import Path


VIDEO_KEYS = (
    "width", "height", "pix_fmt", "color_space", "color_transfer",
    "color_primaries", "r_frame_rate",
)
TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def probe_media(path):
    return json.loads(subprocess.check_output([
        "ffprobe", "-v", "error",
        "-show_entries",
        "stream=codec_type,width,height,pix_fmt,color_space,color_transfer,color_primaries,"
        "r_frame_rate,start_time,duration,sample_rate",
        "-show_entries", "format=duration,size", "-of", "json", str(path),
    ]))


def media_stream(probe, codec_type):
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == codec_type:
            return stream
    raise SystemExit(f"媒体缺少 {codec_type} 流")


def stream_span(stream):
    start = float(stream.get("start_time") or 0.0)
    duration = float(stream["duration"])
    return start, start + duration


def av_sync_metrics(probe):
    video_start, video_end = stream_span(media_stream(probe, "video"))
    audio_start, audio_end = stream_span(media_stream(probe, "audio"))
    return {
        "start_delta_seconds": audio_start - video_start,
        "end_delta_seconds": audio_end - video_end,
        "max_absolute_delta_seconds": max(
            abs(audio_start - video_start), abs(audio_end - video_end)
        ),
    }


def frame_rate(stream):
    numerator, denominator = map(int, stream["r_frame_rate"].split("/"))
    return numerator / denominator


def source_time_to_output(source_time, kept_ranges):
    output_time = 0.0
    for item in kept_ranges:
        start, end = float(item["start"]), float(item["end"])
        rendered_start = float(item.get("output_start", output_time))
        rendered_end = float(item.get("output_end", rendered_start + end - start))
        if source_time >= end:
            output_time = rendered_end
        elif source_time > start:
            output_time = rendered_start + source_time - start
            break
        else:
            break
    return output_time


def sample_times(duration, fps, cutlist):
    frame = 1 / fps
    values = [0.0]
    values.extend(duration * fraction for fraction in (0.2, 0.4, 0.6, 0.8))
    values.append(max(0.0, duration - frame))
    kept = cutlist.get("kept_ranges_seconds", [])
    for repeat in cutlist.get("manual_repeat_ranges_seconds", []):
        boundary = source_time_to_output(float(repeat["start"]), kept)
        values.extend((max(0.0, boundary - 0.2), min(duration - frame, boundary + 0.2)))
    deduplicated = []
    seen_frames = set()
    for value in sorted(values):
        index = max(0, round(value * fps))
        if index not in seen_frames:
            seen_frames.add(index)
            deduplicated.append(index / fps)
    return deduplicated


def last_srt_end(path):
    matches = TIME_RE.findall(Path(path).read_text(encoding="utf-8-sig"))
    if not matches:
        raise ValueError(f"SRT 没有有效时间轴：{path}")
    values = list(map(int, matches[-1][4:]))
    return values[0] * 3600 + values[1] * 60 + values[2] + values[3] / 1000


def select_expression(times, fps):
    return "+".join(f"eq(n\\,{round(value * fps)})" for value in times)


def run_single_decode(media, sheet, times, fps):
    count = len(times)
    columns = min(4, count)
    rows = math.ceil(count / columns)
    scale_width = 480 if count <= 24 else 360
    filters = (
        "[0:v:0]split=2[vcheck][vshots];"
        "[vcheck]null[vdecoded];"
        f"[vshots]select='{select_expression(times, fps)}',"
        f"scale={scale_width}:-2,"
        f"tile=layout={columns}x{rows}:nb_frames={count}:padding=4:margin=4[sheet]"
    )
    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(media),
        "-filter_complex", filters,
        "-map", "[vdecoded]", "-map", "0:a?", "-f", "null", "-",
        "-map", "[sheet]", "-frames:v", "1", "-q:v", "2", str(sheet),
    ]
    subprocess.run(command, check=True)


def inspect(args):
    source_probe = probe_media(args.source)
    output_probe = probe_media(args.output)
    source_stream = media_stream(source_probe, "video")
    output_stream = media_stream(output_probe, "video")
    mismatches = {
        key: {"source": source_stream.get(key), "output": output_stream.get(key)}
        for key in VIDEO_KEYS if source_stream.get(key) != output_stream.get(key)
    }
    if mismatches:
        raise SystemExit(f"媒体属性不一致：{json.dumps(mismatches, ensure_ascii=False)}")

    cutlist = json.loads(args.cutlist.read_text(encoding="utf-8"))
    if (len(cutlist.get("kept_ranges_seconds", [])) > 1 and
            cutlist.get("render_strategy") != "paired_segment_concat_v1"):
        raise SystemExit(
            "cutlist 不是逐段音画成对拼接版本；旧版独立 select/aselect 会累积同步误差"
        )
    expected = float(cutlist.get(
        "expected_output_duration_seconds",
        sum(float(item["end"]) - float(item["start"])
            for item in cutlist["kept_ranges_seconds"]),
    ))
    actual = float(output_probe["format"]["duration"])
    duration_delta = actual - expected
    if abs(duration_delta) > args.duration_tolerance:
        raise SystemExit(
            f"成片时长偏差 {duration_delta:.3f}s 超过容差 {args.duration_tolerance:.3f}s"
        )
    sync = av_sync_metrics(output_probe)
    if sync["max_absolute_delta_seconds"] > args.av_sync_tolerance:
        raise SystemExit(
            "音视频流首尾差最大 "
            f"{sync['max_absolute_delta_seconds']:.3f}s，超过同步容差 "
            f"{args.av_sync_tolerance:.3f}s"
        )
    srt_end = None
    if args.srt:
        srt_end = last_srt_end(args.srt)
        if srt_end > actual + 0.1:
            raise SystemExit(f"SRT 末尾 {srt_end:.3f}s 超过成片 {actual:.3f}s")

    fps = frame_rate(output_stream)
    times = sample_times(actual, fps, cutlist)
    args.sheet.parent.mkdir(parents=True, exist_ok=True)
    run_single_decode(args.output, args.sheet, times, fps)
    report = {
        "pass": True,
        "source": str(args.source),
        "output": str(args.output),
        "matched_video_properties": {key: output_stream.get(key) for key in VIDEO_KEYS},
        "expected_output_duration_seconds": round(expected, 6),
        "actual_output_duration_seconds": round(actual, 6),
        "duration_delta_seconds": round(duration_delta, 6),
        "av_sync": {key: round(value, 6) for key, value in sync.items()},
        "av_sync_tolerance_seconds": args.av_sync_tolerance,
        "srt_end_seconds": round(srt_end, 6) if srt_end is not None else None,
        "contact_sheet": str(args.sheet),
        "sample_times_seconds": [round(value, 6) for value in times],
        "full_decode_pass": True,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cutlist", type=Path, required=True)
    parser.add_argument("--srt", type=Path)
    parser.add_argument("--sheet", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--duration-tolerance", type=float, default=0.5)
    parser.add_argument("--av-sync-tolerance", type=float, default=0.05)
    return parser.parse_args(argv)


def main(argv=None):
    inspect(parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
