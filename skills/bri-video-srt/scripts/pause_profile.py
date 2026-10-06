#!/usr/bin/env python3
"""Measure acoustic speech gaps with a reproducible definition, never SRT cue gaps."""
import argparse
import hashlib
import json
import re
import statistics
import subprocess
from pathlib import Path


def merge_microbreaks(intervals, max_gap=0.06):
    result = []
    for start, end in sorted(intervals):
        if result and start - result[-1][1] <= max_gap + 1e-9:
            result[-1][1] = max(end, result[-1][1])
        else:
            result.append([start, end])
    return result


def detect(media, threshold_db=-35.0, min_silence=0.12, merge_gap=0.06):
    duration = float(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=nw=1:nk=1', str(media)], text=True).strip())
    run = subprocess.run([
        'ffmpeg', '-nostdin', '-hide_banner', '-i', str(media), '-vn',
        '-af', f'aresample=async=1:first_pts=0,aresample=16000,aformat=sample_fmts=s16,silencedetect=noise={threshold_db}dB:d={min_silence}',
        '-f', 'null', '-'], capture_output=True, text=True, check=True)
    intervals, start = [], None
    for line in run.stderr.splitlines():
        match = re.search(r'silence_start: ([0-9.]+)', line)
        if match:
            start = float(match[1])
        match = re.search(r'silence_end: ([0-9.]+)', line)
        if match and start is not None:
            intervals.append([start, min(duration, float(match[1]))])
            start = None
    if start is not None:
        intervals.append([start, duration])
    return duration, merge_microbreaks(intervals, merge_gap)


def summarize(intervals, duration, included_ranges=None, edge_tolerance=0.04):
    """Exclude leading/trailing silence and gaps straddling selected content ranges."""
    included_ranges = included_ranges or [[0.0, duration]]
    values = []
    for start, end in intervals:
        if start <= edge_tolerance or end >= duration - edge_tolerance:
            continue
        if any(left <= start and end <= right for left, right in included_ranges):
            values.append({'start': start, 'end': end, 'duration': end - start})
    durations = [row['duration'] for row in values]
    if not durations:
        raise ValueError('没有符合口径的气口，不能生成节奏标准')
    ordered = sorted(durations)
    return {
        'count': len(durations), 'mean_seconds': statistics.mean(durations),
        'median_seconds': statistics.median(durations),
        'min_seconds': min(durations), 'max_seconds': max(durations),
        'p90_seconds': ordered[round((len(ordered) - 1) * 0.9)],
        'included_ranges_seconds': included_ranges, 'pauses': values,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('media', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--threshold-db', type=float, default=-35.0)
    parser.add_argument('--min-silence', type=float, default=0.12)
    parser.add_argument('--merge-gap', type=float, default=0.06)
    parser.add_argument('--ranges', type=Path, help='JSON list of [start,end] seconds')
    args = parser.parse_args()
    if args.min_silence <= 0 or not 0 <= args.merge_gap <= 0.06:
        parser.error('min-silence 必须为正，merge-gap 必须在 0～0.06 秒')
    duration, intervals = detect(args.media, args.threshold_db, args.min_silence, args.merge_gap)
    included = json.loads(args.ranges.read_text()) if args.ranges else None
    if included is not None and (not included or any(
            len(row) != 2 or not 0 <= row[0] < row[1] <= duration + .05 for row in included)):
        parser.error('ranges 必须是有效的非空媒体时间区间列表')
    report = {
        'schema_version': 1, 'source_name': args.media.name,
        'source_size_bytes': args.media.stat().st_size,
        'duration_seconds': duration,
        'method': {'sample_rate': 16000, 'sample_format': 'PCM16',
                   'threshold_db': args.threshold_db, 'min_silence_seconds': args.min_silence,
                   'merge_gap_seconds': args.merge_gap, 'exclude_head_tail': True,
                   'definition': '可听口播之间的声学停顿，包含句间和短语间停顿；不是字幕时间差'},
        **summarize(intervals, duration, included),
    }
    report['measurement_sha256'] = hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in
                     ('count', 'mean_seconds', 'median_seconds', 'p90_seconds', 'max_seconds')}, indent=2))


if __name__ == '__main__':
    main()
