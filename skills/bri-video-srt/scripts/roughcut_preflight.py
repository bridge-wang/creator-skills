#!/usr/bin/env python3
"""粗剪 4K 导出前后的音频切点门禁。

prepare: 把人工/模型确认的重复口播候选吸附到真实长静音末端，生成
auto-editor 合并 cutlist 与一次性拼接音频预览。
validate: 转写拼接预览后，验证每个切点左右语义锚点；失败即禁止 4K 导出。
validate-final: 4K 成片重新转写后，再次验证所有左右语义锚点。
"""
import argparse
import json
import re
import subprocess
import unicodedata
import wave
from pathlib import Path


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
    merged = []
    for start, end in events:
        if merged and start - merged[-1][1] <= 0.55:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return merged


def resolve_ranges(candidates, intervals):
    resolved = []
    for item in candidates:
        hint = float(item["retain_hint"])
        nearby = [
            (end - start, abs(end - hint), start, end)
            for start, end in intervals
            if abs(end - hint) <= float(item.get("snap_radius", 6.5))
            and end - start >= float(item.get("min_supporting_silence", 1.0))
        ]
        if not nearby:
            raise SystemExit(f"{item['id']}: no qualifying silence near {hint}")
        duration, _, silence_start, silence_end = max(
            nearby, key=lambda value: (value[0], -value[1]))
        resolved.append({
            **item,
            "discard_end": round(silence_end, 6),
            "snap_distance": round(silence_end - hint, 6),
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
        })
        chunks.extend([segment, separator])
        cursor += duration + 0.8
    with wave.open(str(target_wav), "wb") as writer:
        writer.setparams(params)
        writer.writeframes(b"".join(chunks))
    return mapping


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
    Path(args.report).write_text(json.dumps({"resolved_ranges": resolved, "preview_mapping": mapping}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"resolved_ranges": resolved}, ensure_ascii=False, indent=2))


def validate_preview(args):
    report_path = Path(args.report)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    cues, results = parse_srt(args.preview_srt), []
    for item in report["preview_mapping"]:
        text = normalized("".join(cue_text for start, end, cue_text in cues
                                  if item["preview_start"] <= (start + end) / 2 <= item["preview_end"]))
        left = anchors_in_text(text, item["left_anchors"])
        right = anchors_in_text(text, item["right_anchors"])
        results.append({"id": item["id"], "left_ok": bool(left), "right_ok": bool(right),
                        "pass": bool(left) and bool(right)})
    passed = all(result["pass"] for result in results)
    report.update({"preflight_validation": results, "preflight_pass": passed})
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"pass": passed, "results": results}, ensure_ascii=False, indent=2))
    if not passed:
        raise SystemExit(1)


def validate_final(args):
    config = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    text = normalized("".join(cue_text for _, _, cue_text in parse_srt(args.srt)))
    results = []
    for item in config["candidates"]:
        left_matches = anchors_in_text(text, item["left_anchors"])
        right_matches = anchors_in_text(text, item["right_anchors"])
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
                        "pass": bool(left_matches) and bool(right_matches) and pair_ok})
    passed = all(result["pass"] for result in results)
    print(json.dumps({"pass": passed, "results": results}, ensure_ascii=False, indent=2))
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
    final_parser = commands.add_parser("validate-final")
    final_parser.add_argument("--srt", required=True)
    final_parser.add_argument("--candidates", required=True)
    final_parser.set_defaults(func=validate_final)
    arguments = parser.parse_args()
    arguments.func(arguments)


if __name__ == "__main__":
    main()
