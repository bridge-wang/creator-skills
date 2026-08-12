#!/usr/bin/env python3
"""色彩无损删气口：auto-editor 出原始剪切点，按口播节奏补回留白后重编码。

用法：autocut.py <input.MOV> <cutlist_v1.json> <output.mp4> [留白参数]
输出保持源视频的 pix_fmt / color_primaries / color_trc / colorspace 不变。

默认留白来自课程对标视频：句间停顿最多 1.05 秒、片头 0.17 秒、片尾 0.37 秒。
生成 cutlist 时必须给 auto-editor 传 ``--margin 0sec``，避免重复补白。

编码器：macOS 上优先用 hevc_videotoolbox（硬件编码，快）；
不可用时回退 libx265（软件编码，慢但各平台通用）。
"""
import argparse
import json
import subprocess


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("src")
    parser.add_argument("cutlist")
    parser.add_argument("dst")
    parser.add_argument(
        "--report",
        help="可选：写入实际保留/删除时间段的 JSON，供字幕映射和人工复核使用",
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
    args = parser.parse_args()
    for name in ("max_pause", "head_pad", "tail_pad"):
        if getattr(args, name) < 0:
            parser.error(f"--{name.replace('_', '-')} 不能为负数")
    return args


def add_benchmark_padding(raw_keep, duration, max_pause, head_pad, tail_pad):
    """为纯声音片段补回头尾和句间留白，并合并相交区间。"""
    padded = []
    # aselect 以解码后的音频帧为边界，AAC 两端最多会多出约 40 ms；
    # 预留 50 ms 编码余量，确保成片实测静音不超过用户指定上限。
    render_guard = min(0.05, max_pause)
    half_pause = (max_pause - render_guard) / 2
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


args = parse_args()
src, cutlist_path, dst = args.src, args.cutlist, args.dst

probe_data = json.loads(subprocess.check_output([
    "ffprobe", "-v", "error", "-select_streams", "v:0",
    "-show_entries", "stream=r_frame_rate,pix_fmt,color_space,color_primaries,color_transfer",
    "-show_entries", "format=duration", "-of", "json", src]))
probe = probe_data["streams"][0]
duration = float(probe_data["format"]["duration"])
num, den = map(int, probe["r_frame_rate"].split("/"))
fps = num / den

with open(cutlist_path, encoding="utf-8") as cutlist_file:
    chunks = json.load(cutlist_file)["chunks"]
raw_keep = [(s / fps, e / fps) for s, e, speed in chunks if float(speed) == 1.0]
if not raw_keep:
    raise SystemExit("没有可保留的片段")
keep = add_benchmark_padding(
    raw_keep, duration, args.max_pause, args.head_pad, args.tail_pad,
)

vf = "+".join(f"between(t,{s:.4f},{e:.4f})" for s, e in keep)
ten_bit = "10le" in probe.get("pix_fmt", "")

encoders = subprocess.check_output(["ffmpeg", "-v", "error", "-encoders"], text=True)
if "hevc_videotoolbox" in encoders:
    codec = ["-c:v", "hevc_videotoolbox", "-q:v", "60"]
else:
    codec = ["-c:v", "libx265", "-crf", "20", "-preset", "medium"]

cmd = [
    "ffmpeg", "-y", "-v", "error", "-i", src,
    "-vf", f"select='{vf}',setpts=N/FRAME_RATE/TB",
    "-af", f"aselect='{vf}',asetpts=N/SR/TB",
    *codec, "-tag:v", "hvc1",
    "-pix_fmt", "p010le" if ten_bit else "yuv420p",
    "-colorspace", probe.get("color_space", "bt709"),
    "-color_primaries", probe.get("color_primaries", "bt709"),
    "-color_trc", probe.get("color_transfer", "bt709"),
    "-c:a", "aac", "-b:a", "192k",
    dst,
]
subprocess.run(cmd, check=True)
kept_duration = sum(end - start for start, end in keep)
removed = max(0.0, duration - kept_duration)
if args.report:
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
    report = {
        "source": src,
        "output": dst,
        "duration_seconds": round(duration, 6),
        "padding_seconds": {
            "max_pause": args.max_pause,
            "head_pad": args.head_pad,
            "tail_pad": args.tail_pad,
        },
        "kept_ranges_seconds": [
            {"start": round(start, 6), "end": round(end, 6)}
            for start, end in keep
        ],
        "removed_ranges_seconds": removed_ranges,
        "manual_repeat_ranges_seconds": [],
    }
    with open(args.report, "w", encoding="utf-8") as report_file:
        json.dump(report, report_file, ensure_ascii=False, indent=2)
        report_file.write("\n")
print(
    f"完成：保留 {len(keep)} 段，删去 {removed:.1f} 秒气口；"
    f"句间≤{args.max_pause:.2f}s，片头 {args.head_pad:.2f}s，"
    f"片尾 {args.tail_pad:.2f}s → {dst}"
)
if args.report:
    print(f"剪辑时间记录 → {args.report}")
