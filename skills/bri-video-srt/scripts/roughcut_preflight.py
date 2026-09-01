#!/usr/bin/env python3
"""粗剪 4K 导出前后的音频切点门禁。

prepare: 把人工/模型确认的重复口播候选吸附到真实长静音末端，生成
auto-editor 合并 cutlist 与一次性拼接音频预览。
validate: 转写拼接预览后，验证每个切点左右语义锚点；失败即禁止 4K 导出。
validate-final: 4K 成片重新转写后，再次验证所有左右语义锚点。
preview-cutlist: 从已审核 kept_ranges 生成整片 PCM 音频预览，供返修重渲染前验收。
"""
import argparse
import json
import re
import subprocess
import unicodedata
import wave
from pathlib import Path


SILENCE_MERGE_GAP_SECONDS = 0.06


def merge_silences(events, max_gap=SILENCE_MERGE_GAP_SECONDS):
    """只合并检测器造成的微小断裂，不能跨过一个可能存在的低声句首。"""
    merged = []
    for start, end in events:
        if merged and start - merged[-1][1] <= max_gap:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return merged


def detect_silences(media):
    run = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(media),
         "-af", "silencedetect=noise=-35dB:d=0.18", "-f", "null", "-"],
        text=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True,
    )
    events, current = [], None
    for line in run.stderr.splitlines():
        start = re.search(r"silence_start: ([0-9.]+)", line)
        end = re.search(r"silence_end: ([0-9.]+)", line)
        if start:
            current = float(start.group(1))
        elif end and current is not None:
            events.append([current, float(end.group(1))])
            current = None
    return merge_silences(events)


def resolve_ranges(candidates, intervals):
    resolved = []
    for item in candidates:
        hint = float(item["retain_hint"])
        nearby = [
            (abs(end - hint), -(end - start), start, end)
            for start, end in intervals
            if abs(end - hint) <= float(item.get("snap_radius", 6.5))
            and end - start >= float(item.get("min_supporting_silence", 1.0))
        ]
        if not nearby:
            raise SystemExit(f"{item['id']}: no qualifying silence near {hint}")
        # retain_hint 是完整重说开头的语义提示，因此先选结束点离 hint 最近的
        # 合格长静音；距离完全相同时再选更长的静音。旧版“最长优先”可能吸附到
        # 更早的长停顿，第三课实测即因此多跑了一轮预检。
        distance, negative_duration, silence_start, silence_end = min(nearby)
        duration = -negative_duration
        # silencedetect 的尾点可能晚于低声句首。主动保留一段前卷，既保护
        # “所以说”“第一个”等短引导词，也让预览切点与最终切点保持一致。
        retain_preroll = float(item.get("retain_preroll", 0.46))
        discard_end = max(silence_start, silence_end - retain_preroll)
        resolved.append({
            **item,
            "discard_end": round(discard_end, 6),
            "detected_silence_end": round(silence_end, 6),
            "retain_preroll": round(retain_preroll, 6),
            "snap_distance": round(silence_end - hint, 6),
            "snap_selection": "nearest_end_then_longest",
            "supporting_silence": {
                "start": round(silence_start, 6), "end": round(silence_end, 6),
                "duration": round(duration, 6),
            },
        })
    return resolved


def merge_chunks(chunks):
    merged = []
    for start, end, speed in chunks:
        if start >= end:
            continue
        if merged and merged[-1][1] == start and float(merged[-1][2]) == float(speed):
            merged[-1][1] = end
        else:
            merged.append([start, end, speed])
    return merged


def apply_ranges(payload, fps, resolved):
    frame_ranges = [(round(float(i["discard_start"]) * fps),
                     round(float(i["discard_end"]) * fps)) for i in resolved]
    output = []
    for start, end, speed in payload["chunks"]:
        pieces = [(start, end, speed)]
        for cut_start, cut_end in frame_ranges:
            next_pieces = []
            for piece_start, piece_end, piece_speed in pieces:
                if float(piece_speed) != 1.0 or cut_end <= piece_start or cut_start >= piece_end:
                    next_pieces.append((piece_start, piece_end, piece_speed))
                    continue
                if piece_start < cut_start:
                    next_pieces.append((piece_start, min(piece_end, cut_start), piece_speed))
                next_pieces.append((max(piece_start, cut_start), min(piece_end, cut_end), 99999.0))
                if cut_end < piece_end:
                    next_pieces.append((max(piece_start, cut_end), piece_end, piece_speed))
            pieces = next_pieces
        output.extend(pieces)
    payload["chunks"] = merge_chunks(output)
    return payload


def write_preview(source_wav, target_wav, resolved):
    with wave.open(str(source_wav), "rb") as reader:
        params = reader.getparams()
        if params.nchannels != 1 or params.sampwidth != 2:
            raise SystemExit("preview source must be mono PCM16")
        frames = reader.readframes(params.nframes)
    rate, width = params.framerate, params.sampwidth * params.nchannels
    separator = b"\x00" * int(0.8 * rate) * width
    chunks, mapping, cursor = [], [], 0.0
    for item in resolved:
        start, end = float(item["discard_start"]), float(item["discard_end"])
        left_start = max(0.0, start - float(item.get("left_context", 10.0)))
        right_end = end + float(item.get("right_context", 5.0))
        left = frames[int(left_start * rate) * width:int(start * rate) * width]
        right = frames[int(end * rate) * width:int(right_end * rate) * width]
        segment = left + right
        duration = len(segment) / width / rate
        mapping.append({
            "id": item["id"], "preview_start": round(cursor, 6),
            "preview_end": round(cursor + duration, 6),
            "left_anchors": item["left_anchors"], "right_anchors": item["right_anchors"],
            "phrase_counts": item.get("phrase_counts", []),
            "join_time": round(cursor + len(left) / width / rate, 6),
        })
        chunks.extend([segment, separator])
        cursor += duration + 0.8
    with wave.open(str(target_wav), "wb") as writer:
        writer.setparams(params)
        writer.writeframes(b"".join(chunks))
    return mapping


def write_kept_preview(source_wav, target_wav, cutlist):
    """按最终 kept_ranges 拼接 PCM16 音频，避免先付出整片 4K 编码成本。"""
    with wave.open(str(source_wav), "rb") as reader:
        params = reader.getparams()
        if params.nchannels != 1 or params.sampwidth != 2:
            raise SystemExit("preview source must be mono PCM16")
        frames = reader.readframes(params.nframes)
    rate = params.framerate
    frame_width = params.sampwidth * params.nchannels
    chunks = []
    for item in cutlist.get("kept_ranges_seconds", []):
        start = int(round(float(item["start"]) * rate)) * frame_width
        end = int(round(float(item["end"]) * rate)) * frame_width
        chunks.append(frames[start:end])
    if not chunks:
        raise SystemExit("cutlist 没有 kept_ranges_seconds")
    with wave.open(str(target_wav), "wb") as writer:
        writer.setparams(params)
        writer.writeframes(b"".join(chunks))


def seconds(value):
    hours, minutes, rest = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def parse_srt(path):
    cues = []
    for block in re.split(r"\n\s*\n", Path(path).read_text(encoding="utf-8").strip()):
        lines = block.splitlines()
        if len(lines) >= 3 and " --> " in lines[1]:
            start, end = lines[1].split(" --> ")
            cues.append((seconds(start), seconds(end), "".join(lines[2:])))
    return cues


def normalized(value):
    value = unicodedata.normalize("NFKC", value).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)


def anchors_in_text(text, anchors):
    return [normalized(anchor) for anchor in anchors if normalized(anchor) in text]


def phrase_count_results(text, rules):
    results = []
    for rule in rules:
        variants = [normalized(value) for value in rule.get("anchors", []) if normalized(value)]
        counts = {variant: text.count(variant) for variant in variants}
        count = max(counts.values(), default=0)
        minimum = int(rule.get("min", 1))
        maximum = int(rule.get("max", minimum))
        results.append({
            "anchors": rule.get("anchors", []), "count": count,
            "min": minimum, "max": maximum,
            "pass": minimum <= count <= maximum,
        })
    return results


def find_tandem_repeats(text, allowed=None, min_length=6, max_length=40):
    """找相邻的长短语复读；“很多很多”等短修辞不在自动拦截范围。"""
    allowed = {normalized(value) for value in (allowed or [])}
    issues = []
    cursor = 0
    while cursor < len(text):
        largest = min(max_length, (len(text) - cursor) // 2)
        match = None
        for size in range(largest, min_length - 1, -1):
            phrase = text[cursor:cursor + size]
            if phrase == text[cursor + size:cursor + 2 * size] and phrase not in allowed:
                match = phrase
                break
        if match:
            issues.append({"offset": cursor, "text": match, "length": len(match)})
            cursor += len(match) * 2
        else:
            cursor += 1
    return issues


def validate_candidate_text(text, item):
    left = anchors_in_text(text, item["left_anchors"])
    right = anchors_in_text(text, item["right_anchors"])
    counts = phrase_count_results(text, item.get("phrase_counts", []))
    count_ok = all(result["pass"] for result in counts)
    return left, right, counts, count_ok


def prepare(args):
    config = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    payload = json.loads(Path(args.auto_json).read_text(encoding="utf-8"))
    fps_text = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate", "-of", "default=nw=1:nk=1", args.media,
    ], text=True).strip()
    numerator, denominator = map(int, fps_text.split("/"))
    resolved = resolve_ranges(config["candidates"], detect_silences(args.wav))
    combined = apply_ranges(payload, numerator / denominator, resolved)
    Path(args.combined_json).write_text(json.dumps(combined, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mapping = write_preview(args.wav, args.preview_wav, resolved)
    Path(args.report).write_text(json.dumps({
        "resolved_ranges": resolved,
        "preview_mapping": mapping,
        "allowed_tandem_repeats": config.get("allowed_tandem_repeats", []),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"resolved_ranges": resolved}, ensure_ascii=False, indent=2))


def validate_preview(args):
    report_path = Path(args.report)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    cues, results = parse_srt(args.preview_srt), []
    for item in report["preview_mapping"]:
        text = normalized("".join(cue_text for start, end, cue_text in cues
                                  if item["preview_start"] <= (start + end) / 2 <= item["preview_end"]))
        left, right, counts, count_ok = validate_candidate_text(text, item)
        results.append({"id": item["id"], "left_ok": bool(left), "right_ok": bool(right),
                        "phrase_counts": counts,
                        "pass": bool(left) and bool(right) and count_ok})
    full_text = normalized("".join(cue_text for _, _, cue_text in cues))
    duplicates = find_tandem_repeats(full_text, report.get("allowed_tandem_repeats"))
    passed = all(result["pass"] for result in results) and not duplicates
    report.update({"preflight_validation": results, "tandem_repeats": duplicates,
                   "preflight_pass": passed})
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"pass": passed, "results": results,
                      "tandem_repeats": duplicates}, ensure_ascii=False, indent=2))
    if not passed:
        raise SystemExit(1)


def validate_final(args):
    config = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    text = normalized("".join(cue_text for _, _, cue_text in parse_srt(args.srt)))
    results = []
    for item in config["candidates"]:
        left_matches, right_matches, counts, count_ok = validate_candidate_text(text, item)
        pair_ok, distance = False, None
        for left in left_matches:
            left_position = text.find(left)
            for right in right_matches:
                right_position = text.find(right, left_position + len(left))
                if right_position >= 0:
                    candidate_distance = right_position - left_position - len(left)
                    if candidate_distance <= int(item.get("max_intervening_chars", 60)):
                        pair_ok, distance = True, candidate_distance
                        break
            if pair_ok:
                break
        results.append({"id": item["id"], "left_ok": bool(left_matches),
                        "right_ok": bool(right_matches), "ordered_nearby_pair": pair_ok,
                        "intervening_normalized_chars": distance,
                        "phrase_counts": counts,
                        "pass": bool(left_matches) and bool(right_matches) and pair_ok and count_ok})
    duplicates = find_tandem_repeats(text, config.get("allowed_tandem_repeats"))
    passed = all(result["pass"] for result in results) and not duplicates
    print(json.dumps({"pass": passed, "results": results,
                      "tandem_repeats": duplicates}, ensure_ascii=False, indent=2))
    if not passed:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    for name in ("media", "wav", "candidates", "auto_json", "combined_json", "preview_wav", "report"):
        prepare_parser.add_argument(f"--{name.replace('_', '-')}", dest=name, required=True)
    prepare_parser.set_defaults(func=prepare)
    preview_parser = commands.add_parser("validate")
    preview_parser.add_argument("--preview-srt", required=True)
    preview_parser.add_argument("--report", required=True)
    preview_parser.set_defaults(func=validate_preview)
    kept_parser = commands.add_parser("preview-cutlist")
    kept_parser.add_argument("--wav", required=True)
    kept_parser.add_argument("--cutlist", required=True)
    kept_parser.add_argument("--preview-wav", required=True)
    kept_parser.set_defaults(func=lambda args: write_kept_preview(
        args.wav, args.preview_wav,
        json.loads(Path(args.cutlist).read_text(encoding="utf-8")),
    ))
    final_parser = commands.add_parser("validate-final")
    final_parser.add_argument("--srt", required=True)
    final_parser.add_argument("--candidates", required=True)
    final_parser.set_defaults(func=validate_final)
    arguments = parser.parse_args()
    arguments.func(arguments)


if __name__ == "__main__":
    main()
