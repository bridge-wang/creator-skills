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
import tempfile
from datetime import datetime, timezone

try:
    from PIL import Image
    from render import ASSETS, contact_sheet, digest, fit_title, load_style, render, verify_assets
except ImportError as error:
    raise SystemExit(f'Required dependency missing: {error}. Use an existing Python with Pillow.')


DELIVERY_SIZES = {'3x4': (1440, 1920), '9x16': (1080, 1920),
                  '16x9': (1920, 1080), '4x3': (1440, 1080)}
DELIVERY_NAMES = {f'封面-{ratio}.png' for ratio in DELIVERY_SIZES} | {'总预览图.jpg'}


def delivery_path(video, supplied=None):
    today = datetime.now().astimezone()
    path = (Path(supplied).expanduser().absolute() if supplied else
            Path(video).parent / f'{today.month}月{today.day}号-封面')
    if not re.fullmatch(r'(?:[1-9]|1[0-2])月(?:[1-9]|[12][0-9]|3[01])号-封面', path.name):
        raise ValueError('Delivery folder must be named M月D号-封面, for example 10月6号-封面')
    if path.is_symlink():
        raise ValueError('Delivery folder must not be a symlink')
    return path.resolve()


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
    delivery = delivery_path(video, args.out)
    if delivery.exists() and not args.replace:
        raise ValueError('Delivery folder already exists; use --replace only for an authorized revision')
    root = Path(tempfile.mkdtemp(prefix='bri-cover-work-')).resolve()
    job = {'schema': 'bri-cover-generate/1', 'created': datetime.now(timezone.utc).isoformat(),
           'delivery_directory': str(delivery), 'replace_delivery': args.replace,
           'source_video': str(video), 'source_signature': signature(video), 'video': info,
           'raw_title': args.title, 'literal_middle_dot': args.literal_middle_dot,
           'title_lines': lines, 'style': load_style(), 'state': 'prepared', 'owned': {},
           'candidates': [], 'selected': None}
    save_job(root, job)
    times = [round(info['duration'] * (.03 + .94 * i / (args.samples - 1)), 3) for i in range(args.samples)]
    result = sample(job, root, times)
    result.update(job=str(root / 'job.json'), delivery_directory=str(delivery),
                  title_lines=lines, video=info, state='prepared')
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
        if variant.get('crop_anchor_override') is not None:
            variant['crop_anchor'] = variant['crop_anchor_override']
        elif variant['ratio'] in ('3x4', '9x16'):
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
    return {'state': job['state'], 'review_directory': str(directory), 'overview': str(sheet),
            'covers': records, 'next': 'Visually verify these images, then run deliver to export exactly five images and delete all working files.'}


def deliver(args):
    root, job = load_job(args.job)
    if job['state'] != 'finalized':
        raise ValueError('Delivery requires completed, visually verified final covers')
    destination = delivery_path(job['source_video'], args.out or job.get('delivery_directory'))
    if destination.is_relative_to(root) or root.is_relative_to(destination):
        raise ValueError('Delivery and working directories must be separate')
    if Path(job['source_video']).resolve().is_relative_to(root):
        raise ValueError('Refusing to purge a working directory containing the source video')
    # Only delete a complete, unmodified job inventory, including old revisions.
    for path in root.rglob('*'):
        relative = str(path.relative_to(root))
        safe_path(root, relative)
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f'Unexpected working file type: {relative}')
        if relative == 'job.json' or path.name == '.DS_Store':
            continue
        meta = job['owned'].get(relative)
        if not meta or digest(path) != meta['sha256']:
            raise ValueError(f'Untracked or modified working file; resolve before delivery: {relative}')
    manifest = json.loads(safe_path(root, job['final_manifest']).read_text())
    if (len(manifest['covers']) != 4 or
            {cover['ratio'] for cover in manifest['covers']} != set(DELIVERY_SIZES)):
        raise ValueError('Delivery requires exactly the four standard ratios')
    exports = []
    for cover in manifest['covers']:
        path = safe_path(root, cover['file'])
        if digest(path) != cover['sha256']:
            raise ValueError('Final cover changed or is missing; delivery stopped')
        with Image.open(path) as image:
            if image.size != DELIVERY_SIZES[cover['ratio']] or image.format != 'PNG':
                raise ValueError('Final cover has incorrect dimensions or format')
            image.verify()
        exports.append((path, f"封面-{cover['ratio']}.png", cover['sha256']))
    overview = safe_path(root, job['final_overview'])
    if digest(overview) != job['owned'][job['final_overview']]['sha256']:
        raise ValueError('Overview changed or is missing')
    with Image.open(overview) as image:
        if image.format != 'JPEG':
            raise ValueError('Overview must be JPEG')
        image.verify()
    exports.append((overview, '总预览图.jpg', digest(overview)))
    replace = args.replace or job.get('replace_delivery', False)
    if destination.exists():
        if not replace:
            raise ValueError('Delivery folder already exists; explicit replacement is required')
        if (not destination.is_dir() or {p.name for p in destination.iterdir()} != DELIVERY_NAMES or
                any(not p.is_file() or p.is_symlink() for p in destination.iterdir())):
            raise ValueError('Existing delivery folder contains unexpected files; no replacement performed')
    report = {'output_directory': str(destination), 'files': sorted(DELIVERY_NAMES),
              'working_directory_removed': str(root), 'applied': not args.dry_run}
    if args.dry_run:
        return report
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.bri-cover-export-', dir=destination.parent))
    try:
        for source_file, name, expected_hash in exports:
            target = staging / name
            shutil.copyfile(source_file, target)
            if digest(target) != expected_hash:
                raise ValueError('Export hash mismatch; working files retained')
        backup = None
        if destination.exists():
            backup = Path(tempfile.mkdtemp(prefix='.bri-cover-old-', dir=destination.parent))
            backup.rmdir()
            destination.rename(backup)
        try:
            staging.rename(destination)
        except OSError:
            if backup is not None:
                backup.rename(destination)
            raise
        if backup is not None:
            shutil.rmtree(backup)
        shutil.rmtree(root)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return report


def cleanup(args):
    # Backward-compatible command name; no longer keeps raw frames or metadata.
    args.dry_run = not args.apply
    return deliver(args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('doctor', help='Check installed dependencies and audited assets')
    prep = commands.add_parser('prepare', help='Sample video; do not select for the user')
    prep.add_argument('--video', required=True)
    prep.add_argument('--title', required=True)
    prep.add_argument('--out')
    prep.add_argument('--replace', action='store_true', help='Authorized revision of an existing five-image delivery')
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
    delivery = commands.add_parser('deliver', help='After visual review, export five images and delete all working files')
    delivery.add_argument('--job', required=True)
    delivery.add_argument('--out')
    delivery.add_argument('--replace', action='store_true')
    delivery.add_argument('--dry-run', action='store_true')
    clean = commands.add_parser('cleanup', help='Compatibility alias for deliver; --apply exports and purges the whole job')
    clean.add_argument('--job', required=True)
    clean.add_argument('--out')
    clean.add_argument('--replace', action='store_true')
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
    elif args.command == 'deliver':
        result = deliver(args)
    else:
        _, result = load_job(args.job)
    emit(result)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        sys.exit(2)
