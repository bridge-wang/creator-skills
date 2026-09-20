"""Deterministic cover composition with one audited, bundled font."""
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import math
from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).resolve().parents[1] / 'assets'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def load_style():
    return json.loads((ASSETS / 'style.json').read_text())


@lru_cache(maxsize=8)
def font(size):
    return ImageFont.truetype(str(ASSETS / 'fonts/SourceHanSerifSC-Bold.otf'), size,
                              layout_engine=ImageFont.Layout.BASIC)


def verify_assets(style, lines=()):
    for asset, check in [('font_file', 'font_sha256'), ('license_file', 'license_sha256'),
                         ('icc_file', 'icc_sha256')]:
        if digest(ASSETS / style[asset]) != style[check]:
            raise ValueError(f'Asset fingerprint mismatch: {style[asset]}; no font fallback allowed')
    if font(100).getname() != (style['font_family'], style['font_style']):
        raise ValueError('Unexpected font identity')
    missing = font(100).getmask('\U0010ffff')
    for char in set(''.join(lines)):
        glyph = font(100).getmask(char)
        if glyph.size == missing.size and bytes(glyph) == bytes(missing):
            raise ValueError(f'Unsupported title glyph: {char!r}; do not replace the font silently')
    alpha = style['black_overlay_opacity']
    if not math.isfinite(alpha) or not 0 <= alpha <= 1:
        raise ValueError('Black overlay opacity must be between 0 and 1')


def line_mask(text, face, style):
    size = face.size
    mask = Image.new('L', (round((len(text) + 2) * size * 1.6), size * 3), 0)
    draw = ImageDraw.Draw(mask)
    x = size / 2
    for i, char in enumerate(text):
        draw.text((x, size / 2), char, fill=255, font=face)
        x += face.getlength(char)
        if i < len(text) - 1:
            x += size * style['letter_spacing_em']
            if (ord(char) > 127) != (ord(text[i + 1]) > 127):
                x += size * style['cjk_latin_extra_spacing_em']
    bounds = mask.getbbox()
    if not bounds:
        raise ValueError('Empty title line')
    return mask.crop(bounds)


def fit_title(lines, variant, style):
    width, height = variant['size']
    best = None
    pinned = variant.get('font_size_px')
    for size in ([pinned] if pinned else range(48, 401)):
        masks = [line_mask(text, font(size), style) for text in lines]
        gap = round(max(m.height for m in masks) * style['ink_line_gap_ratio'])
        ink_height = sum(m.height for m in masks) + gap * (len(masks) - 1)
        if (max(m.width for m in masks) > width * variant['max_title_width_ratio'] or
                ink_height > height * variant['max_title_height_ratio']):
            if pinned:
                raise ValueError('Pinned font size does not fit')
            break
        best = size, masks, gap, ink_height
    if best is None:
        raise ValueError('Title too long for readable layout; review semantic line breaks or shorten with user')
    return best


def cover_fit(frame, size, anchor):
    if len(anchor) != 2 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in anchor):
        raise ValueError('Crop anchor must contain two values between 0 and 1')
    width, height = size
    ratio = width / height
    if frame.width / frame.height > ratio:
        crop_height, crop_width = frame.height, frame.height * ratio
    else:
        crop_width, crop_height = frame.width, frame.width / ratio
    left = (frame.width - crop_width) * anchor[0]
    top = (frame.height - crop_height) * anchor[1]
    box = (left, top, left + crop_width, top + crop_height)
    return frame.resize(tuple(size), Image.Resampling.LANCZOS, box=box), list(box)


def render(frame, lines, style, variant, path):
    width, height = variant['size']
    mode = variant['background']
    if mode == 'auto_landscape':
        mode = 'duplicate_side_by_side' if frame.width < frame.height else 'single'
    if mode == 'duplicate_side_by_side':
        panel, box = cover_fit(frame, (width // 2, height), variant['crop_anchor'])
        background = Image.new('RGB', (width, height))
        background.paste(panel, (0, 0))
        background.paste(panel, (width // 2, 0))
        crops = [{'source_xyxy': box, 'destination_xywh': [x, 0, width // 2, height]}
                 for x in (0, width // 2)]
    elif mode == 'single':
        background, box = cover_fit(frame, (width, height), variant['crop_anchor'])
        crops = [{'source_xyxy': box, 'destination_xywh': [0, 0, width, height]}]
    else:
        raise ValueError(f'Unknown background mode: {mode}')
    canvas = Image.blend(background, Image.new('RGB', background.size, 'black'),
                         style['black_overlay_opacity'])
    size, masks, gap, total_height = fit_title(lines, variant, style)
    y = round(height * style['title_center'][1] - total_height / 2)
    boxes = []
    for text, mask in zip(lines, masks):
        x = round(width * style['title_center'][0] - mask.width / 2)
        bounds = [x, y, x + mask.width, y + mask.height]
        if not (0 <= x < bounds[2] <= width and 0 <= y < bounds[3] <= height):
            raise ValueError('Clipped title')
        canvas.paste(style['text_color'], tuple(bounds), mask)
        boxes.append({'text': text, 'ink_box': bounds})
        y += mask.height + gap
    canvas.save(path, icc_profile=(ASSETS / style['icc_file']).read_bytes())
    return {'ratio': variant['ratio'], 'size': [width, height], 'sha256': digest(path),
            'font_size_px': size, 'letter_spacing_px': size * style['letter_spacing_em'],
            'line_gap_px': gap, 'lines': boxes, 'background': mode,
            'background_crops': crops, 'black_overlay_opacity': style['black_overlay_opacity']}


def contact_sheet(items, path, columns=3, cell=(360, 480)):
    """items: pairs of readable file path and label; aspect ratios are preserved."""
    from PIL import ImageOps
    width, height = cell
    rows = math.ceil(len(items) / columns)
    sheet = Image.new('RGB', (columns * (width + 24) + 24, rows * (height + 66) + 24), '#efeeeb')
    draw = ImageDraw.Draw(sheet)
    for i, (file, label) in enumerate(items):
        x, y = 24 + (i % columns) * (width + 24), 24 + (i // columns) * (height + 66)
        with Image.open(file) as source:
            thumb = ImageOps.contain(source.convert('RGB'), cell, Image.Resampling.LANCZOS)
        sheet.paste(thumb, (x + (width - thumb.width) // 2, y + (height - thumb.height) // 2))
        draw.text((x, y + height + 10), label, font=font(22), fill='#252525')
    sheet.save(path, quality=93, subsampling=0, icc_profile=(ASSETS / 'sRGB.icc').read_bytes())
