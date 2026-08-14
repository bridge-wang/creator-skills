#!/usr/bin/env python3
"""粗剪前审计长静音：用画面和语义区分操作等待与普通口播停顿。

prepare 从 auto-editor 原始 cutlist 中列出长静音，附上前后 SRT 语义，并按每段
“停顿前 / 开始 / 中间 / 结束 / 停顿后”五帧生成联系表。模型或人工查看联系表后填写 classification
和 reason。apply 只在所有候选都已分类时继续：普通口播停顿交给 autocut 压缩；
操作型空白必须提供正向画面证据和最小保护窗口，保护窗口总计最多 7 秒。候选内部若
可能存在低声口播则完整保留，避免把人的话误当空白。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


PROTECTED_CLASSES = {
    "operation",
    "wait_generation",
    "page_switch",
    "typing_or_input",
    "quiet_speech",
}
REVIEW_CLASSES = {"retake_context", "uncertain"}
COMPRESS_CLASSES = {"ordinary_speech_pause"} | REVIEW_CLASSES
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
    media_end = max(end_frame for _, end_frame, _ in payload["chunks"]) / fps
    last_frame_time = max(0.0, media_end - 1 / fps)
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
            "sample_labels": [
                "context_before", "pause_start", "pause_middle", "pause_end", "context_after",
            ],
            "sample_times": [
                round(max(0.0, start - 0.2), 6),
                round(min(end, start + 0.1), 6),
                round((start + end) / 2, 6),
                round(max(start, end - 0.1), 6),
                round(min(last_frame_time, end + 0.2), 6),
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
        columns = 5
        rows = len(group)
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
            "layout": "每行一个候选，从左到右为停顿前/开始/中间/结束/停顿后",
        })
    return sheets


def merge_ranges(ranges):
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def take_frame_budget(ranges, budget, reverse=False):
    selected = []
    sequence = list(reversed(ranges)) if reverse else ranges
    for start, end in sequence:
        if budget <= 0:
            break
        size = min(end - start, budget)
        selected.append((end - size, end) if reverse else (start, start + size))
        budget -= size
    return list(reversed(selected)) if reverse else selected


def protected_retention_frames(item, fps, max_protected_pause):
    """返回受保护候选需要恢复的最小帧段。"""
    candidate_start = round(float(item["start"]) * fps)
    candidate_end = round(float(item["end"]) * fps)
    explicit = item.get("protected_ranges_seconds") or []
    ranges = merge_ranges([
        (round(float(value["start"]) * fps), round(float(value["end"]) * fps))
        for value in explicit
    ]) if explicit else [(candidate_start, candidate_end)]
    if item.get("possible_quiet_speech") or max_protected_pause is None:
        return ranges
    cap_frames = max(0, round(float(max_protected_pause) * fps))
    total_frames = sum(end - start for start, end in ranges)
    if total_frames <= cap_frames:
        return ranges
    left_frames = cap_frames // 2
    right_frames = cap_frames - left_frames
    return merge_ranges(
        take_frame_budget(ranges, left_frames)
        + take_frame_budget(ranges, right_frames, reverse=True)
    )


def is_protected(item):
    return bool(item.get("possible_quiet_speech")) or item["classification"] in PROTECTED_CLASSES


def apply_protected_ranges(payload, fps, candidates, max_protected_pause=7.0):
    protected = [item for item in candidates if is_protected(item)]
    retention = [
        interval
        for item in protected
        for interval in protected_retention_frames(item, fps, max_protected_pause)
        if interval[1] > interval[0]
    ]
    output = []
    for start, end, speed in payload["chunks"]:
        boundaries = {start, end}
        for left, right in retention:
            if left < end and right > start:
                boundaries.add(max(start, left))
                boundaries.add(min(end, right))
        points = sorted(boundaries)
        for left, right in zip(points, points[1:]):
            restored = float(speed) != 1.0 and any(
                keep_start < right and keep_end > left
                for keep_start, keep_end in retention
            )
            output.append([left, right, 1.0 if restored else speed])
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
        if (classification in PROTECTED_CLASSES - {"quiet_speech"}
                and not item.get("possible_quiet_speech")):
            if not str(item.get("visual_evidence", "")).strip():
                errors.append(f"{item.get('id')}: 保护分类必须填写 visual_evidence")
            ranges = item.get("protected_ranges_seconds")
            if not isinstance(ranges, list) or not ranges:
                errors.append(f"{item.get('id')}: 保护分类必须填写 protected_ranges_seconds")
                continue
            for index, value in enumerate(ranges, start=1):
                try:
                    start, end = float(value["start"]), float(value["end"])
                except (KeyError, TypeError, ValueError):
                    errors.append(f"{item.get('id')}: 保护窗口 {index} 格式无效")
                    continue
                if start < float(item["start"]) or end > float(item["end"]) or end <= start:
                    errors.append(f"{item.get('id')}: 保护窗口 {index} 必须位于候选内且 end > start")
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
            item["visual_evidence"] = decision.get("visual_evidence", "")
            item["protected_ranges_seconds"] = decision.get("protected_ranges_seconds", [])
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
            "rule": (
                "普通口播停顿压到 1.05 秒；操作型候选必须记录正向画面证据和最小保护窗口，"
                "窗口总计最多保留 7 秒；uncertain/retake_context 默认压缩；"
                "可能存在低声口播时完整保留。"
            ),
        },
        "contact_sheets": sheets,
        "candidates": candidates,
        "audit_pass": False,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidates": len(candidates), "contact_sheets": sheets}, ensure_ascii=False, indent=2))


def apply(args):
    if args.max_protected_pause < 0:
        raise SystemExit("--max-protected-pause 不能为负数")
    payload = json.loads(args.auto_json.read_text(encoding="utf-8"))
    report = json.loads(args.report.read_text(encoding="utf-8"))
    candidates = report.get("candidates", [])
    if args.decisions:
        decisions = json.loads(args.decisions.read_text(encoding="utf-8"))
        candidates = merge_decisions(candidates, decisions)
        report["candidates"] = candidates
    validate_classifications(candidates)
    fps = float(report["fps"])
    combined = apply_protected_ranges(
        payload, fps, candidates, max_protected_pause=args.max_protected_pause,
    )
    protected = [item for item in candidates if is_protected(item)]
    for item in protected:
        retention = protected_retention_frames(item, fps, args.max_protected_pause)
        uncapped_retention = protected_retention_frames(item, fps, None)
        item["retained_ranges_seconds"] = [
            {"start": round(start / fps, 6), "end": round(end / fps, 6)}
            for start, end in retention
        ]
        item["retained_duration_seconds"] = round(
            sum(end - start for start, end in retention) / fps, 6
        )
        item["capped_to_seconds"] = (
            args.max_protected_pause
            if (not item.get("possible_quiet_speech")
                and sum(end - start for start, end in uncapped_retention)
                > round(args.max_protected_pause * fps))
            else None
        )
        item["partial_protection"] = bool(item.get("protected_ranges_seconds")) and (
            item["retained_duration_seconds"] + 1e-6 < float(item["duration"])
        )
    capped = [item for item in protected if item["capped_to_seconds"] is not None]
    report.update({
        "audit_pass": True,
        "max_protected_pause_seconds": args.max_protected_pause,
        "protected_ranges": protected,
        "protected_count": len(protected),
        "capped_protected_count": len(capped),
        "compressed_count": len(candidates) - len(protected),
    })
    args.output_json.write_text(json.dumps(combined, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "audit_pass": True,
        "protected_count": len(protected),
        "capped_protected_count": len(capped),
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
    apply_parser.add_argument(
        "--max-protected-pause", type=float, default=7.0,
        help="确认无低声口播的操作型空白最多保留秒数（默认：7.0，首尾均分）",
    )
    apply_parser.set_defaults(func=apply)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
