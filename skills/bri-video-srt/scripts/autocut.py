#!/usr/bin/env python3
"""色彩保真删气口：按声音区间补回留白后，只执行一次 4K 重编码。

用法：autocut.py <input.MOV> <cutlist_v1.json> <output.mp4> [留白参数]
输出保持源视频的 pix_fmt / color_primaries / color_trc / colorspace 不变。

每个保留区间先统一量化到源视频帧边界，再成对 trim 视频与音频，最后使用
concat 同步拼接。不要用 select/aselect 分别压缩两条时间轴：视频帧与 AAC 音频帧
的取整粒度不同，多切点时会把每段误差累积成肉眼可见的口型错位。

进度以预计成片时长为分母，而不是原片时长。ffmpeg 收尾可能把
``out_time_ms`` 写成 ``N/A``；这种状态只表示进度值不可用，不应让已经完成的
编码和后续 cutlist 报告失败。
"""
import argparse
import json
import math
import subprocess
import sys
from pathlib import Path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("src")
    parser.add_argument("cutlist")
    parser.add_argument("dst")
    parser.add_argument(
        "--reuse-report", action="store_true",
        help="把第二个参数当作既有 autocut 报告，复用已审核的 kept_ranges 重新渲染",
    )
    parser.add_argument(
        "--report",
        help="可选：写入实际保留/删除时间段的 JSON，供字幕映射和人工复核使用",
    )
    parser.add_argument(
        "--preflight-report",
        help="可选：roughcut_preflight.py 产生且已 N/N 通过的报告；把重复区间写入最终 cutlist",
    )
    parser.add_argument(
        "--max-pause", type=float, default=1.05,
        help="长静音剪短后保留的最大句间停顿秒数（默认：1.05）",
    )
    parser.add_argument(
        "--head-pad", type=float, default=0.17,
        help="第一处声音前保留的片头静音秒数（默认：0.17）",
    )
    parser.add_argument(
        "--tail-pad", type=float, default=0.37,
        help="最后一处声音后保留的片尾静音秒数（默认：0.37）",
    )
    args = parser.parse_args(argv)
    for name in ("max_pause", "head_pad", "tail_pad"):
        if getattr(args, name) < 0:
            parser.error(f"--{name.replace('_', '-')} 不能为负数")
    return args


def add_benchmark_padding(raw_keep, duration, max_pause, head_pad, tail_pad):
    """为纯声音片段补回头尾和句间留白，并合并相交区间。"""
    padded = []
    # 音画统一量化到视频帧边界后，不再需要为 aselect 的独立取整预留余量。
    half_pause = max_pause / 2
    last_index = len(raw_keep) - 1
    for index, (start, end) in enumerate(raw_keep):
        left = head_pad if index == 0 else half_pause
        right = tail_pad if index == last_index else half_pause
        start = max(0.0, start - left)
        end = min(duration, end + right)
        if padded and start <= padded[-1][1]:
            padded[-1] = (padded[-1][0], max(padded[-1][1], end))
        else:
            padded.append((start, end))
    return padded


def parse_progress_seconds(key, value):
    """解析 ffmpeg progress；N/A、空值和非有限值都安全忽略。"""
    if key not in {"out_time_ms", "out_time_us"}:
        return None
    try:
        microseconds = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(microseconds) or microseconds < 0:
        return None
    return microseconds / 1_000_000


def progress_percent(seconds, expected_duration):
    """编码未收尾前最多显示 99%；100% 只在 ffmpeg 成功退出后打印。"""
    if seconds is None or expected_duration <= 0:
        return None
    return min(99, max(0, int(seconds / expected_duration * 100)))


def quantize_keep_ranges(keep, fps):
    """把切点统一到视频帧边界，并生成精确的输出时间轴。"""
    ranges = []
    output_cursor = 0.0
    for start, end in keep:
        start_frame = max(0, math.ceil(start * fps - 1e-9))
        end_frame = max(start_frame + 1, math.ceil(end * fps - 1e-9))
        source_start = start_frame / fps
        source_end = end_frame / fps
        segment_duration = (end_frame - start_frame) / fps
        ranges.append({
            "start_frame": start_frame,
            "end_frame": end_frame,
            "source_start": source_start,
            "source_end": source_end,
            "output_start": output_cursor,
            "output_end": output_cursor + segment_duration,
        })
        output_cursor += segment_duration
    return ranges


def build_paired_concat_filter(ranges):
    """为所有片段构造单次解码、逐段音画成对拼接的 filter graph。"""
    count = len(ranges)
    video_inputs = "".join(f"[v{index}]" for index in range(count))
    audio_inputs = "".join(f"[a{index}]" for index in range(count))
    filters = [
        f"[0:v:0]setpts=PTS-STARTPTS,split={count}{video_inputs}",
        f"[0:a:0]asetpts=PTS-STARTPTS,asplit={count}{audio_inputs}",
    ]
    concat_inputs = []
    for index, item in enumerate(ranges):
        duration = item["output_end"] - item["output_start"]
        filters.append(
            f"[v{index}]trim=start_frame={item['start_frame']}:end_frame={item['end_frame']},"
            f"setpts=PTS-STARTPTS[vt{index}]"
        )
        filters.append(
            f"[a{index}]atrim=start={item['source_start']:.9f}:end={item['source_end']:.9f},"
            f"asetpts=PTS-STARTPTS,apad,atrim=duration={duration:.9f}[at{index}]"
        )
        concat_inputs.append(f"[vt{index}][at{index}]")
    filters.append(
        "".join(concat_inputs) + f"concat=n={count}:v=1:a=1[vout][aout]"
    )
    return ";".join(filters)


def write_cutlist_report(path, src, dst, duration, args, ranges, preflight,
                         requested_duration=None, previous_report=None):
    keep = [(item["source_start"], item["source_end"]) for item in ranges]
    removed_ranges = []
    cursor = 0.0
    for start, end in keep:
        if start > cursor:
            removed_ranges.append({
                "start": round(cursor, 6),
                "end": round(start, 6),
                "duration": round(start - cursor, 6),
                "reason": "auto-editor silence detection",
            })
        cursor = max(cursor, end)
    if cursor < duration:
        removed_ranges.append({
            "start": round(cursor, 6),
            "end": round(duration, 6),
            "duration": round(duration - cursor, 6),
            "reason": "auto-editor silence detection",
        })
    manual_repeats = []
    if preflight:
        manual_repeats = [
            {
                "id": item["id"],
                "start": round(float(item["discard_start"]), 6),
                "end": round(float(item["discard_end"]), 6),
                "duration": round(float(item["discard_end"]) - float(item["discard_start"]), 6),
                "reason": "high-confidence repeated/abandoned take; preflight passed",
                "supporting_silence": item.get("supporting_silence"),
            }
            for item in preflight.get("resolved_ranges", [])
        ]
    elif previous_report:
        manual_repeats = previous_report.get("manual_repeat_ranges_seconds", [])
    report = {
        "source": src,
        "output": dst,
        "duration_seconds": round(duration, 6),
        "requested_output_duration_seconds": round(requested_duration, 6)
        if requested_duration is not None else None,
        "expected_output_duration_seconds": round(
            sum(item["output_end"] - item["output_start"] for item in ranges), 6
        ),
        "render_strategy": "paired_segment_concat_v1",
        "padding_seconds": {
            "max_pause": args.max_pause,
            "head_pad": args.head_pad,
            "tail_pad": args.tail_pad,
        },
        "kept_ranges_seconds": [{
            "start": round(item["source_start"], 6),
            "end": round(item["source_end"], 6),
            "output_start": round(item["output_start"], 6),
            "output_end": round(item["output_end"], 6),
        } for item in ranges],
        "removed_ranges_seconds": removed_ranges,
        "manual_repeat_ranges_seconds": manual_repeats,
        "preflight_pass": (
            preflight.get("preflight_pass") if preflight
            else previous_report.get("preflight_pass") if previous_report else None
        ),
    }
    Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    args = parse_args(argv)
    src, cutlist_path, dst = args.src, args.cutlist, args.dst

    preflight = None
    if args.preflight_report:
        preflight = json.loads(Path(args.preflight_report).read_text(encoding="utf-8"))
        if preflight.get("preflight_pass") is not True:
            raise SystemExit("预检报告未 N/N 通过，禁止启动 4K 导出")

    probe_data = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate,pix_fmt,color_space,color_primaries,color_transfer",
        "-show_entries", "format=duration", "-of", "json", src,
    ]))
    probe = probe_data["streams"][0]
    duration = float(probe_data["format"]["duration"])
    num, den = map(int, probe["r_frame_rate"].split("/"))
    fps = num / den

    cutlist_payload = json.loads(Path(cutlist_path).read_text(encoding="utf-8"))
    previous_report = cutlist_payload if args.reuse_report else None
    if args.reuse_report:
        raw_keep = [
            (float(item["start"]), float(item["end"]))
            for item in cutlist_payload.get("kept_ranges_seconds", [])
        ]
    else:
        chunks = cutlist_payload["chunks"]
        raw_keep = [
            (start / fps, end / fps)
            for start, end, speed in chunks if float(speed) == 1.0
        ]
    if not raw_keep:
        raise SystemExit("没有可保留的片段")
    keep = raw_keep if args.reuse_report else add_benchmark_padding(
        raw_keep, duration, args.max_pause, args.head_pad, args.tail_pad,
    )
    requested_duration = sum(end - start for start, end in keep)
    ranges = quantize_keep_ranges(keep, fps)
    expected_duration = sum(
        item["output_end"] - item["output_start"] for item in ranges
    )
    filter_graph = build_paired_concat_filter(ranges)
    ten_bit = "10le" in probe.get("pix_fmt", "")
    encoders = subprocess.check_output(["ffmpeg", "-v", "error", "-encoders"], text=True)
    if "hevc_videotoolbox" in encoders:
        codec = ["-c:v", "hevc_videotoolbox", "-q:v", "60"]
    else:
        codec = ["-c:v", "libx265", "-crf", "20", "-preset", "medium"]

    cmd = [
        "ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1", "-i", src,
        "-filter_complex", filter_graph,
        "-map", "[vout]", "-map", "[aout]",
        *codec, "-tag:v", "hvc1",
        "-pix_fmt", "p010le" if ten_bit else "yuv420p",
        "-colorspace", probe.get("color_space", "bt709"),
        "-color_primaries", probe.get("color_primaries", "bt709"),
        "-color_trc", probe.get("color_transfer", "bt709"),
        "-c:a", "aac", "-b:a", "192k", dst,
    ]
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
        last_percent = -1
        for line in process.stdout:
            key, _, value = line.strip().partition("=")
            seconds = parse_progress_seconds(key, value)
            percent = progress_percent(seconds, expected_duration)
            if percent is not None and percent >= last_percent + 2:
                print(f"4K 导出进度：{percent}%", file=sys.stderr, flush=True)
                last_percent = percent
        stderr = process.stderr.read()
        return_code = process.wait()
    if return_code:
        if stderr:
            print(stderr, file=sys.stderr)
        raise SystemExit(return_code)
    print("4K 导出进度：100%", file=sys.stderr, flush=True)

    removed = max(0.0, duration - expected_duration)
    if args.report:
        write_cutlist_report(
            args.report, src, dst, duration, args, ranges, preflight,
            requested_duration=requested_duration, previous_report=previous_report,
        )
    print(
        f"完成：保留 {len(keep)} 段，删去 {removed:.1f} 秒气口；"
        f"句间≤{args.max_pause:.2f}s，片头 {args.head_pad:.2f}s，"
        f"片尾 {args.tail_pad:.2f}s → {dst}"
    )
    if args.report:
        print(f"剪辑时间记录 → {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
