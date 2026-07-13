#!/usr/bin/env python3
"""色彩无损删气口：auto-editor 出剪切点，ffmpeg 按原色域/位深重编码剪切。

用法：autocut.py <input.MOV> <cutlist_v1.json> <output.mp4>
输出保持源视频的 pix_fmt / color_primaries / color_trc / colorspace 不变。

编码器：macOS 上优先用 hevc_videotoolbox（硬件编码，快）；
不可用时回退 libx265（软件编码，慢但各平台通用）。
"""
import json, subprocess, sys

src, cutlist_path, dst = sys.argv[1], sys.argv[2], sys.argv[3]

probe = json.loads(subprocess.check_output([
    "ffprobe", "-v", "error", "-select_streams", "v:0",
    "-show_entries", "stream=r_frame_rate,pix_fmt,color_space,color_primaries,color_transfer",
    "-of", "json", src]))["streams"][0]
num, den = map(int, probe["r_frame_rate"].split("/"))
fps = num / den

chunks = json.load(open(cutlist_path))["chunks"]
keep = [(s / fps, e / fps) for s, e, speed in chunks if speed == 1.0]
if not keep:
    sys.exit("没有可保留的片段")

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
removed = sum(e - s for s, e, sp in chunks if sp != 1.0) / fps
print(f"完成：保留 {len(keep)} 段，删去 {removed:.1f} 秒气口 → {dst}")
