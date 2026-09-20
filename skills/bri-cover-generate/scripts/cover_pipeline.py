#!/usr/bin/env python3
"""Local two-stage video cover pipeline. Run --help for commands."""
import argparse
import copy
import json
import math
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
from datetime import datetime, timezone

try:
    from PIL import Image
    from render import ASSETS, contact_sheet, digest, fit_title, load_style, render, verify_assets
except ImportError as error:
    raise SystemExit(f'Required dependency missing: {error}. Use an existing Python with Pillow.')


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def run(args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f'{Path(args[0]).name} failed: {result.stderr[-1600:]}')
    return result.stdout


def doctor():
    for tool in ('ffmpeg', 'ffprobe'):
        if not shutil.which(tool):
            raise ValueError(f'{tool} is unavailable; use the installed media environment')
    verify_assets(load_style())
    return {'python': sys.version.split()[0], 'pillow': Image.__version__,
            'ffmpeg': run(['ffmpeg', '-version']).splitlines()[0], 'font_assets': 'verified'}


def signature(path):
    stat = path.stat()
    return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def probe(path):
    result = json.loads(run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                             '-show_streams', '-show_format', '-of', 'json', str(path)]))
    if not result['streams']:
        raise ValueError('Input has no video stream')
    stream = result['streams'][0]
    duration = float(stream.get('duration') or result['format'].get('duration', 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Input has no usable duration')
    if stream.get('color_transfer') in ('smpte2084', 'arib-std-b67'):
        raise ValueError('HDR input needs an explicitly reviewed SDR conversion before making covers')
    if stream.get('sample_aspect_ratio', '1:1') not in ('1:1', 'N/A', '0:1'):
        raise ValueError('Non-square pixels require display-aspect normalization first')
    rotation = float(stream.get('tags', {}).get('rotate', 0))
    for side in stream.get('side_data_list', []):
        rotation = float(side.get('rotation', rotation))
    width, height = stream['width'], stream['height']
    if abs(rotation) % 180 == 90:
        width, height = height, width
    elif abs(rotation) % 180:
        raise ValueError('Non-right-angle rotation requires normalization first')
    return {'duration': duration, 'display_size': [width, height], 'rotation': rotation,
            'fps': stream.get('avg_frame_rate'), 'color_transfer': stream.get('color_transfer')}


def title_lines(raw, literal=False, supplied=None):
    marked = raw if literal else raw.replace('·', '\n')
    lines = [line.strip() for line in (supplied if supplied is not None else marked.splitlines())]
    if not lines or len(lines) > 4 or any(not line for line in lines):
        raise ValueError('Title must have 1–4 nonempty lines; use semantic line breaks')
    if len(''.join(lines)) > 120:
        raise ValueError('Title is too long for a cover; ask for a shorter title')
    if supplied is not None and re.sub(r'\s', '', ''.join(lines)) != re.sub(r'\s', '', marked):
        raise ValueError('Line-break adjustment must preserve the supplied title characters')
    verify_assets(load_style(), lines)
    return lines


def safe_path(root, relative):
    path = root / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('Job asset must use a safe relative path')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Job asset escapes output directory')
    if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
        raise ValueError('Symlinked job assets are not accepted')
    return path


def track(job, root, path, role):
    relative = str(path.relative_to(root))
    safe_path(root, relative)
    job['owned'][relative] = {'sha256': digest(path), 'role': role}
    return relative


def load_job(path):
    path = Path(path).expanduser().resolve()
    job = json.loads(path.read_text())
    if job.get('schema') != 'bri-cover-generate/1':
        raise ValueError('Not a supported bri-cover-generate job')
    return path.parent, job


def save_job(root, job):
    write_json(root / 'job.json', job)


def source(job):
    path = Path(job['source_video'])
    if not path.is_file() or signature(path) != job['source_signature']:
        raise ValueError('Source video is missing or changed; prepare a new job')
    return path


def check_times(times, duration):
    if len(set(times)) != len(times) or any(not math.isfinite(t) or not 0 <= t < duration for t in times):
        raise ValueError('Times must be distinct finite seconds within the video')


def extract(video, time, output, crop=None, thumbnail=False):
    filters = []
    if crop:
        x, y, width, height = crop
        filters.append(f'crop={width}:{height}:{x}:{y}')
    if thumbnail:
        filters.append('scale=360:480:force_original_aspect_ratio=decrease')
    args = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-n', '-ss', str(time),
            '-threads', '4', '-i', str(video), '-map', '0:v:0', '-frames:v', '1']
    if filters:
        args += ['-vf', ','.join(filters)]
    run(args + [str(output)])
    if not output.is_file():
        raise ValueError(f'No frame at {time:.3f}s')


def new_revision(root, prefix):
    number = 1
    while (root / f'{prefix}-{number:03}').exists():
        number += 1
    path = root / f'{prefix}-{number:03}'
    path.mkdir()
    return path


def suggest_crop(files, display_size):
    """Conservative edge darkness estimate. Agent must visually approve it."""
    measurements = []
    for file in files:
        with Image.open(file) as image:
            gray = image.convert('L')
        width, height = gray.size
        pixels = gray.load()
        def dark_row(y):
            return sum(pixels[x, y] < 20 for x in range(width)) / width >= .98
        def dark_col(x):
            return sum(pixels[x, y] < 20 for y in range(height)) / height >= .98
        edges = []
        for limit, length, test, reverse in [(height // 4, height, dark_row, False),
                (height // 4, height, dark_row, True), (width // 4, width, dark_col, False),
                (width // 4, width, dark_col, True)]:
            count = 0
            for i in range(limit):
                if not test(length - 1 - i if reverse else i):
                    break
                count += 1
            edges.append(count / length)
        measurements.append(edges)
    common = []
    for axis in range(4):
        values = [m[axis] for m in measurements]
        median = statistics.median(values)
        common.append(median if sum(abs(v - median) < .008 for v in values) >= len(values) * .85 else 0)
    width, height = display_size
    top, bottom, left, right = [round(v * size / 2) * 2 for v, size in zip(common, [height, height, width, width])]
    if top + bottom > height * .4 or left + right > width * .4:
        return [0, 0, width, height]
    return [left, top, width - left - right, height - top - bottom]


def sample(job, root, times):
    check_times(times, job['video']['duration'])
    video = source(job)
    directory = new_revision(root, 'samples')
    items = []
    for time in times:
        path = directory / f'{time:010.3f}s.jpg'
        extract(video, time, path, thumbnail=True)
        track(job, root, path, 'temporary')
        items.append((path, f'{time:.3f}s'))
    sheet = directory / 'contact-sheet.jpg'
    contact_sheet(items, sheet, columns=6, cell=(180, 240))
    track(job, root, sheet, 'evidence')
    crop = suggest_crop([p for p, _ in items], job['video']['display_size'])
    job.setdefault('sample_sets', []).append({'times': times, 'sheet': str(sheet.relative_to(root)),
                                             'suggested_crop_xywh': crop})
    save_job(root, job)
    return {'contact_sheet': str(sheet), 'suggested_crop_xywh': crop, 'visual_review_required': True}


def prepare(args):
    doctor()
    video = Path(args.video).expanduser().resolve()
    if not video.is_file():
        raise ValueError('Video file does not exist')
    lines = title_lines(args.title, args.literal_middle_dot)
    info = probe(video)
    if not 3 <= args.samples <= 60:
        raise ValueError('Sample count must be 3–60')
    root = Path(args.out).expanduser().resolve() if args.out else video.with_name(video.stem + '-封面')
    if root.exists():
        raise ValueError('Output directory already exists; choose a new directory or resume its job.json')
    root.mkdir(parents=True)
    job = {'schema': 'bri-cover-generate/1', 'created': datetime.now(timezone.utc).isoformat(),
           'source_video': str(video), 'source_signature': signature(video), 'video': info,
           'raw_title': args.title, 'literal_middle_dot': args.literal_middle_dot,
           'title_lines': lines, 'style': load_style(), 'state': 'prepared', 'owned': {},
           'candidates': [], 'selected': None}
    for name in ('LICENSE.txt', 'provenance.json'):
        path = root / ('font-' + name)
        shutil.copy2(ASSETS / 'fonts' / name, path)
        track(job, root, path, 'evidence')
    save_job(root, job)
    times = [round(info['duration'] * (.03 + .94 * i / (args.samples - 1)), 3) for i in range(args.samples)]
    result = sample(job, root, times)
    result.update(job=str(root / 'job.json'), title_lines=lines, video=info, state='prepared')
    return result


def candidates(args):
    root, job = load_job(args.job)
    check_times(args.times, job['video']['duration'])
    if len(args.times) != 3:
        raise ValueError('Exactly three candidate times are required')
    lines = title_lines(job['raw_title'], job['literal_middle_dot'],
                        json.loads(args.lines_json) if args.lines_json else job['title_lines'])
    style = copy.deepcopy(job['style'])
    if args.opacity is not None:
        style['black_overlay_opacity'] = args.opacity
    verify_assets(style, lines)
    variant = copy.deepcopy(style['variants'][0])
    variant['crop_anchor'] = args.anchor
    fit_title(lines, variant, style)
    width, height = job['video']['display_size']
    crop = args.crop or [0, 0, width, height]
    x, y, cw, ch = crop
    if min(x, y) < 0 or min(cw, ch) <= 0 or x + cw > width or y + ch > height or any(v % 2 for v in crop):
        raise ValueError('Crop must be an even, positive rectangle inside the display frame')
    directory = new_revision(root, 'candidates')
    video = source(job)
    records, items = [], []
    for label, time in zip('ABC', args.times):
        raw = directory / f'{label}-{time:.3f}s-raw.png'
        extract(video, time, raw, crop=crop)
        raw_rel = track(job, root, raw, 'raw')
        path = directory / f'{label}-3x4.png'
        with Image.open(raw) as image:
            metadata = render(image.convert('RGB'), lines, style, variant, path)
        relative = track(job, root, path, 'draft')
        records.append({'label': label, 'timestamp': time, 'raw': raw_rel,
                        'raw_sha256': digest(raw), 'preview': relative, 'render': metadata,
                        'crop_xywh': crop, 'portrait_anchor': args.anchor})
        items.append((path, f'{label}  {time:.3f}s'))
    sheet = directory / '候选总览.jpg'
    contact_sheet(items, sheet)
    track(job, root, sheet, 'evidence')
    job.update(candidates=records, state='candidates_ready', selected=None, title_lines=lines, style=style)
    save_job(root, job)
    return {'state': job['state'], 'job': str(root / 'job.json'), 'overview': str(sheet),
            'candidates': records, 'next': 'Show candidates and wait for the user to choose A, B, or C.'}


def finalize(args):
    root, job = load_job(args.job)
    if job['state'] not in ('candidates_ready', 'finalized'):
        raise ValueError('Generate and show candidates before finalizing')
    record = next((c for c in job['candidates'] if c['label'] == args.pick.upper()), None)
    if record is None:
        raise ValueError('Pick must identify one of the current candidates')
    raw = safe_path(root, record['raw'])
    if not raw.is_file() or digest(raw) != record['raw_sha256']:
        raise ValueError('Selected raw frame is missing or changed; regenerate candidates and visually verify')
    style = copy.deepcopy(job['style'])
    if args.opacity is not None:
        style['black_overlay_opacity'] = args.opacity
    verify_assets(style, job['title_lines'])
    variants = copy.deepcopy(style['variants'])
    for variant in variants:
        if variant['ratio'] in ('3x4', '9x16'):
            variant['crop_anchor'] = record['portrait_anchor']
        elif args.landscape_anchor:
            variant['crop_anchor'] = args.landscape_anchor
        fit_title(job['title_lines'], variant, style)
    directory = new_revision(root, 'final')
    frame = Image.open(raw).convert('RGB')
    records, items = [], []
    for variant in variants:
        path = directory / f"封面-{variant['ratio']}.png"
        meta = render(frame, job['title_lines'], style, variant, path)
        meta['file'] = track(job, root, path, 'final')
        records.append(meta)
        width, height = variant['size']
        items.append((path, f"{variant['ratio'].replace('x', ':')}   {width} × {height}"))
    sheet = directory / '四比例总览.jpg'
    contact_sheet(items, sheet, columns=2, cell=(576, 540))
    track(job, root, sheet, 'final')
    manifest = {'source_frame': record, 'title_lines': job['title_lines'], 'style': style,
                'variants': variants, 'covers': records, 'source_signature': job['source_signature']}
    path = directory / 'manifest.json'
    write_json(path, manifest)
    track(job, root, path, 'evidence')
    job.update(state='finalized', selected=record['label'], style=style,
               final_manifest=str(path.relative_to(root)), final_overview=str(sheet.relative_to(root)))
    save_job(root, job)
    return {'state': job['state'], 'output_directory': str(directory), 'overview': str(sheet), 'covers': records}


def cleanup(args):
    root, job = load_job(args.job)
    if job['state'] != 'finalized':
        raise ValueError('Cleanup requires completed, verified final covers')
    manifest = json.loads(safe_path(root, job['final_manifest']).read_text())
    for cover in manifest['covers']:
        if digest(safe_path(root, cover['file'])) != cover['sha256']:
            raise ValueError('Final cover changed or is missing; cleanup stopped')
    selected = next(c['raw'] for c in job['candidates'] if c['label'] == job['selected'])
    planned, skipped = [], []
    source_file = Path(job['source_video']).resolve()
    for relative, meta in job['owned'].items():
        if meta['role'] not in ('temporary', 'draft', 'raw') or relative == selected:
            continue
        path = safe_path(root, relative)
        if path.resolve() == source_file:
            raise ValueError('Refusing to remove the original video')
        if not path.exists():
            continue
        if digest(path) != meta['sha256']:
            skipped.append({'file': relative, 'reason': 'modified_since_generation'})
            continue
        planned.append(relative)
    report = {'applied': args.apply, 'files': planned, 'skipped': skipped, 'kept_selected_frame': selected}
    if args.apply:
        for relative in planned:
            safe_path(root, relative).unlink()
        job.setdefault('cleanup_history', []).append(report)
        save_job(root, job)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('doctor', help='Check installed dependencies and audited assets')
    prep = commands.add_parser('prepare', help='Sample video; do not select for the user')
    prep.add_argument('--video', required=True)
    prep.add_argument('--title', required=True)
    prep.add_argument('--out')
    prep.add_argument('--samples', type=int, default=24)
    prep.add_argument('--literal-middle-dot', action='store_true')
    refine = commands.add_parser('sample', help='Inspect extra time points before choosing candidates')
    refine.add_argument('--job', required=True)
    refine.add_argument('--times', type=float, nargs='+', required=True)
    draft = commands.add_parser('candidates', help='Make three 3:4 previews from reviewed time points')
    draft.add_argument('--job', required=True)
    draft.add_argument('--times', type=float, nargs=3, required=True)
    draft.add_argument('--crop', type=int, nargs=4, metavar=('X', 'Y', 'W', 'H'))
    draft.add_argument('--anchor', type=float, nargs=2, default=[.5, .5])
    draft.add_argument('--lines-json', help='Change line breaks while preserving title characters')
    draft.add_argument('--opacity', type=float)
    final = commands.add_parser('finalize', help='Run only after the user identifies a candidate')
    final.add_argument('--job', required=True)
    final.add_argument('--pick', required=True, choices=['A', 'B', 'C', 'a', 'b', 'c'])
    final.add_argument('--opacity', type=float)
    final.add_argument('--landscape-anchor', type=float, nargs=2)
    clean = commands.add_parser('cleanup', help='Remove only tracked, unchanged intermediate files')
    clean.add_argument('--job', required=True)
    clean.add_argument('--apply', action='store_true')
    status = commands.add_parser('status', help='Resume a saved job')
    status.add_argument('--job', required=True)
    args = parser.parse_args()
    if args.command == 'doctor':
        result = doctor()
    elif args.command == 'prepare':
        result = prepare(args)
    elif args.command == 'sample':
        root, job = load_job(args.job)
        result = sample(job, root, args.times)
    elif args.command == 'candidates':
        result = candidates(args)
    elif args.command == 'finalize':
        result = finalize(args)
    elif args.command == 'cleanup':
        result = cleanup(args)
    else:
        _, result = load_job(args.job)
    emit(result)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        sys.exit(2)
