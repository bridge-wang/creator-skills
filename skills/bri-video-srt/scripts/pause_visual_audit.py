#!/usr/bin/env python3
"""粗剪前审计长静音：用画面和语义区分操作等待与普通口播停顿。

prepare 从 auto-editor 原始 cutlist 中列出长静音，附上前后 SRT 语义，并按每段
“开始 / 中间 / 结束”三帧生成联系表。模型或人工查看联系表后填写 classification
和 reason。apply 只在所有候选都已分类时继续，并把操作、等待、输入、页面切换
以及不确定区间恢复为完整保留；普通口播停顿仍交给 autocut 压缩。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from pathlib import Path


PROTECTED_CLASSES = {
    "operation",
    "wait_generation",
    "page_switch",
    "typing_or_input",
    "quiet_speech",
    "retake_context",
    "uncertain",
}
COMPRESS_CLASSES = {"ordinary_speech_pause"}
ALLOWED_CLASSES = PROTECTED_CLASSES | COMPRESS_CLASSES
OPERATION_RE = re.compile(
    r"打开|点击|点开|粘贴|输入|发送|提交|等待|回答|返回|切换|跳转|滚动|"
    r"下载|上传|验证码|生成|加载|刷新|来看|看一看|复制|选择|新建"
)
TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def parse_time(values):
    hour, minute, second, millis = map(int, values)
    return hour * 3600 + minute * 60 + second + millis / 1000


def parse_srt(path):
    cues = []
    raw = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3:
            continue
        match = next((TIME_RE.search(line) for line in lines if TIME_RE.search(line)), None)
        if not match:
            continue
        time_index = next(index for index, line in enumerate(lines) if TIME_RE.search(line))
        cues.append((
            parse_time(match.groups()[:4]),
            parse_time(match.groups()[4:]),
            " ".join(lines[time_index + 1:]),
        ))
    return cues


def frame_rate(media):
    value = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate", "-of", "default=nw=1:nk=1",
        str(media),
    ], text=True).strip()
    numerator, denominator = map(int, value.split("/"))
    return numerator / denominator


def detect_silences(media):
    result = subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-i", str(media),
        "-af", "silencedetect=noise=-35dB:d=0.18", "-f", "null", "-",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=True)
    starts, intervals = [], []
    for line in result.stderr.splitlines():
        start = re.search(r"silence_start:\s*([0-9.]+)", line)
        end = re.search(r"silence_end:\s*([0-9.]+)", line)
        if start:
            starts.append(float(start.group(1)))
        elif end and starts:
            intervals.append((starts.pop(0), float(end.group(1))))
    return intervals


def interval_coverage(start, end, intervals):
    covered = sum(max(0.0, min(end, right) - max(start, left))
                  for left, right in intervals)
    return min(1.0, covered / (end - start)) if end > start else 0.0


def surrounding_text(cues, start, end, count=2):
    before = [text for cue_start, cue_end, text in cues if cue_end <= start + 0.3][-count:]
    after = [text for cue_start, cue_end, text in cues if cue_start >= end - 0.3][:count]
    return before, after


def build_candidates(payload, fps, cues, minimum_duration, silences=None):
    silences = silences or []
    candidates = []
    for start_frame, end_frame, speed in payload["chunks"]:
        if float(speed) == 1.0:
            continue
        start, end = start_frame / fps, end_frame / fps
        if end - start < minimum_duration:
            continue
        before, after = surrounding_text(cues, start, end)
        inside = [text for cue_start, cue_end, text in cues
                  if start <= (cue_start + cue_end) / 2 <= end]
        silence_ratio = interval_coverage(start, end, silences)
        context = " ".join(before + inside + after)
        candidates.append({
            "id": f"pause_{len(candidates) + 1:03d}",
            "start": round(start, 6),
            "end": round(end, 6),
            "duration": round(end - start, 6),
            "before_text": before,
            "inside_text": inside,
            "after_text": after,
            "contains_asr_midpoint": bool(inside),
            "confirmed_silence_ratio": round(silence_ratio, 6),
            "possible_quiet_speech": bool(inside) and silence_ratio < 0.9,
            "semantic_operation_signal": bool(OPERATION_RE.search(context)),
            "sample_times": [
                round(min(end, start + 0.1), 6),
                round((start + end) / 2, 6),
                round(max(start, end - 0.1), 6),
            ],
            "classification": None,
            "reason": "",
        })
    return candidates


def select_expression(times, fps):
    return "+".join(f"eq(n\\,{round(value * fps)})" for value in times)


def render_contact_sheets(media, candidates, fps, sheet_dir, group_size=10):
    sheet_dir.mkdir(parents=True, exist_ok=True)
    sheets = []
    for offset in range(0, len(candidates), group_size):
        group = candidates[offset:offset + group_size]
        times = [value for item in group for value in item["sample_times"]]
        columns = 6
        rows = math.ceil(len(times) / columns)
        target = sheet_dir / f"pause-audit-{offset // group_size + 1:02d}.jpg"
        filters = (
            f"select='{select_expression(times, fps)}',scale=400:-2,"
            "drawtext=text='%{pts\\:hms}':x=8:y=8:fontsize=24:fontcolor=white:"
            "box=1:boxcolor=black@0.7,"
            f"tile=layout={columns}x{rows}:nb_frames={len(times)}:padding=3:margin=3"
        )
        subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(media),
            "-vf", filters, "-frames:v", "1", "-q:v", "2", str(target),
        ], check=True)
        sheets.append({
            "path": str(target),
            "candidate_ids": [item["id"] for item in group],
            "layout": "每个候选连续三帧（开始/中间/结束），每行两个候选",
        })
    return sheets


def apply_protected_ranges(payload, fps, candidates):
    protected = [item for item in candidates if item["classification"] in PROTECTED_CLASSES]
    output = []
    for start, end, speed in payload["chunks"]:
        restored = float(speed) != 1.0 and any(
            round(float(item["start"]) * fps) < end
            and round(float(item["end"]) * fps) > start
            for item in protected
        )
        output.append([start, end, 1.0 if restored else speed])
    merged = []
    for start, end, speed in output:
        if merged and merged[-1][1] == start and float(merged[-1][2]) == float(speed):
            merged[-1][1] = end
        else:
            merged.append([start, end, speed])
    return {**payload, "chunks": merged}


def validate_classifications(candidates):
    errors = []
    for item in candidates:
        classification = item.get("classification")
        if classification not in ALLOWED_CLASSES:
            errors.append(f"{item.get('id')}: classification 未填写或无效")
        if not str(item.get("reason", "")).strip():
            errors.append(f"{item.get('id')}: reason 不能为空")
        if item.get("possible_quiet_speech") and classification == "ordinary_speech_pause":
            errors.append(
                f"{item.get('id')}: 候选内部可能存在低声口播，不能按普通静音压缩"
            )
    if errors:
        raise SystemExit("\n".join(errors))


def merge_decisions(candidates, decisions):
    by_id = {item["id"]: item for item in decisions.get("decisions", [])}
    unknown = sorted(set(by_id) - {item["id"] for item in candidates})
    if unknown:
        raise SystemExit(f"决策包含未知候选：{', '.join(unknown)}")
    for item in candidates:
        decision = by_id.get(item["id"])
        if decision:
            item["classification"] = decision.get("classification")
            item["reason"] = decision.get("reason", "")
    return candidates


def prepare(args):
    payload = json.loads(args.auto_json.read_text(encoding="utf-8"))
    fps = frame_rate(args.media)
    candidates = build_candidates(
        payload, fps, parse_srt(args.srt), args.minimum_duration,
        silences=detect_silences(args.media),
    )
    sheets = render_contact_sheets(args.media, candidates, fps, args.sheet_dir)
    report = {
        "media": str(args.media),
        "auto_json": str(args.auto_json),
        "fps": fps,
        "minimum_duration_seconds": args.minimum_duration,
        "classification_guide": {
            "protected": sorted(PROTECTED_CLASSES),
            "compressed": sorted(COMPRESS_CLASSES),
            "rule": "画面有操作价值或无法确定时完整保留；只有普通口播停顿才压缩。",
        },
        "contact_sheets": sheets,
        "candidates": candidates,
        "audit_pass": False,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidates": len(candidates), "contact_sheets": sheets}, ensure_ascii=False, indent=2))


def apply(args):
    payload = json.loads(args.auto_json.read_text(encoding="utf-8"))
    report = json.loads(args.report.read_text(encoding="utf-8"))
    candidates = report.get("candidates", [])
    if args.decisions:
        decisions = json.loads(args.decisions.read_text(encoding="utf-8"))
        candidates = merge_decisions(candidates, decisions)
        report["candidates"] = candidates
    validate_classifications(candidates)
    fps = float(report["fps"])
    combined = apply_protected_ranges(payload, fps, candidates)
    protected = [item for item in candidates if item["classification"] in PROTECTED_CLASSES]
    report.update({
        "audit_pass": True,
        "protected_ranges": protected,
        "protected_count": len(protected),
        "compressed_count": len(candidates) - len(protected),
    })
    args.output_json.write_text(json.dumps(combined, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "audit_pass": True,
        "protected_count": len(protected),
        "compressed_count": len(candidates) - len(protected),
        "output_json": str(args.output_json),
    }, ensure_ascii=False, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--media", type=Path, required=True)
    prepare_parser.add_argument("--auto-json", type=Path, required=True)
    prepare_parser.add_argument("--srt", type=Path, required=True)
    prepare_parser.add_argument("--report", type=Path, required=True)
    prepare_parser.add_argument("--sheet-dir", type=Path, required=True)
    prepare_parser.add_argument("--minimum-duration", type=float, default=1.5)
    prepare_parser.set_defaults(func=prepare)
    apply_parser = commands.add_parser("apply")
    apply_parser.add_argument("--auto-json", type=Path, required=True)
    apply_parser.add_argument("--report", type=Path, required=True)
    apply_parser.add_argument("--output-json", type=Path, required=True)
    apply_parser.add_argument("--decisions", type=Path)
    apply_parser.set_defaults(func=apply)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
