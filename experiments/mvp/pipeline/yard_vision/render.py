"""Generated elevated yard views and overlays; metadata is scoring-only ground truth."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


def _dict(value: Any) -> dict:
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else dict(value)


def _font(size: int):
    for name in ('/System/Library/Fonts/Supplemental/Arial.ttf', 'DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def _color(box_id: str) -> tuple[int, int, int]:
    palette = [(166, 55, 48), (43, 102, 137), (60, 117, 83), (189, 133, 41),
               (127, 72, 113), (77, 118, 126), (141, 77, 47)]
    return palette[hashlib.sha256(box_id.encode()).digest()[0] % len(palette)]


def _shade(color, factor):
    return tuple(min(255, int(v * factor)) for v in color)


def render_yard(state, yard, path, *, title='Generated synthetic yard') -> dict:
    """Draw all ground slots, preserving bay/row addresses and bottom-to-top tiers.

    Rows have sufficient camera spacing that every tier's side remains visible.
    This is a deliberately clean test arm, not a calibrated real camera model.
    ``yard`` may be a Yard or an Instance (or either model's JSON dump).
    Returns JSON metadata; never send this metadata to a recovery model.
    """
    state, yard = _dict(state), _dict(yard)
    if 'port' in yard:
        yard = yard['port']['yard']
    if state.get('quay_buffer'):
        raise ValueError('render_yard does not render quay_buffer boxes')
    stacks = {s['key']: s for s in state.get('stacks', [])}
    if len(stacks) != len(state.get('stacks', [])):
        raise ValueError('duplicate stack key')
    blocks = sorted(yard['blocks'], key=lambda b: b['id'])
    if not blocks:
        raise ValueError('yard has no blocks')
    tier_h, bay_w, side_dx, side_dy, margin = 32, 138, 22, 16, 44
    max_bays = max(b['n_bays'] for b in blocks)
    width = max(680, margin * 2 + max_bays * bay_w + side_dx)
    layouts, y = [], 96
    for block in blocks:
        pitch = block['max_tiers'] * tier_h + side_dy + 64
        layouts.append((block, y, pitch))
        y += 42 + block['n_rows'] * pitch + 22
    image = Image.new('RGB', (width, y + 110), '#d7dbd9')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 73), fill='#163444')
    draw.text((margin, 12), title, fill='white', font=_font(23))
    draw.text((margin, 43), 'Generated picture | Synthetic ships and cargo, simulated port',
              fill='#dbe9ee', font=_font(15))
    boxes, slots, rendered = [], [], set()
    for block, top, pitch in layouts:
        draw.text((margin, top), f"BLOCK {block['id']} | bays increase left to right | rows back to front",
                  font=_font(16), fill='#173440')
        for row in range(1, block['n_rows'] + 1):
            base_y = top + 32 + row * pitch - 38
            for bay in range(1, block['n_bays'] + 1):
                key = f"{block['id']}-{bay:03d}-{row:02d}"
                x = margin + (bay - 1) * bay_w
                ground = [[x, base_y], [x + bay_w - 8, base_y],
                          [x + bay_w - 8 + side_dx, base_y - side_dy], [x + side_dx, base_y - side_dy]]
                draw.polygon(ground, outline='#f8f5cd', width=2)
                draw.text((x + 5, base_y + 9), key, fill='#263e44', font=_font(13))
                slots.append({'key': key, 'polygon': ground})
            # Ground paint must precede every box; a 40' box spans two slots.
            for bay in range(1, block['n_bays'] + 1):
                key = f"{block['id']}-{bay:03d}-{row:02d}"
                x = margin + (bay - 1) * bay_w
                stack = stacks.get(key)
                if stack is None:
                    continue
                rendered.add(key)
                count = len(stack['boxes'])
                length = stack['length_ft']
                if length not in block.get('allowed_lengths_ft', [20, 40, 45]):
                    raise ValueError(f'disallowed length at {key}')
                span = 1 if length == 20 else 2
                if count > block['max_tiers'] or bay + span - 1 > block['n_bays']:
                    raise ValueError(f'stack outside geometry: {key}')
                box_w = bay_w * span - side_dx - 8
                for tier, box_id in enumerate(stack['boxes'], 1):
                    bottom = base_y - (tier - 1) * tier_h
                    high = bottom - tier_h
                    color = _color(box_id)
                    front = [[x, bottom], [x + box_w, bottom], [x + box_w, high], [x, high]]
                    side = [[x + box_w, bottom], [x + box_w + side_dx, bottom - side_dy],
                            [x + box_w + side_dx, high - side_dy], [x + box_w, high]]
                    roof = [[x, high], [x + box_w, high],
                            [x + box_w + side_dx, high - side_dy], [x + side_dx, high - side_dy]]
                    draw.polygon(front, fill=color, outline='#203744', width=2)
                    draw.polygon(side, fill=_shade(color, .72), outline='#203744', width=2)
                    draw.polygon(roof, fill=_shade(color, 1.24), outline='#203744', width=2)
                    for rib in range(8, box_w - 4, 9):
                        draw.line((x + rib, high + 3, x + rib, bottom - 3), fill=_shade(color, .81))
                    label = f'{box_id}'
                    font = _font(14)
                    while draw.textlength(label, font=font) > box_w - 12 and font.size > 8:
                        font = _font(font.size - 1)
                    text_width = draw.textlength(label, font=font)
                    tx, ty = x + (box_w - text_width) / 2, high + 8
                    draw.rectangle((tx - 3, ty - 2, tx + text_width + 3, ty + font.size + 2), fill=color)
                    draw.text((tx, ty), label, fill='white', font=font, stroke_width=0)
                    polygon = [[x, bottom], [x + box_w, bottom],
                               [x + box_w + side_dx, bottom - side_dy],
                               [x + box_w + side_dx, high - side_dy],
                               ([x + side_dx, high - side_dy] if tier == count else [x + box_w, high]),
                               [x, high]]
                    boxes.append({'id': box_id, 'key': key, 'tier': tier, 'length_ft': length,
                                  'polygon': polygon, 'front_polygon': front,
                                  'bbox': [x, high - side_dy, x + box_w + side_dx, bottom]})
    unknown = set(stacks) - rendered
    if unknown:
        raise ValueError(f'unknown stack addresses: {sorted(unknown)}')
    quay = [margin, y, width - margin, y + 72]
    draw.rectangle(quay, fill='#c3cac7', outline='#f8f5cd', width=3)
    draw.text((margin + 13, y + 23), 'QUAY BUFFER | visibly empty',
              font=_font(18), fill='#173440')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return {'schema_version': 'stowlm.yard-render/1', 'mode': 'generated', 'synthetic': True,
            'path': str(path), 'width': width, 'height': image.height,
            'projection': 'elevated separated rows; not a calibrated camera',
            'boxes': boxes, 'slots': slots, 'box_count': len(boxes), 'stack_count': len(stacks),
            'quay_buffer': {'bbox': quay, 'boxes': []}}


def overlay_segments(image_path, segments, path, *, title='SAM 3.1 outlines | Generated picture') -> dict:
    """Overlay model evidence only. Boxes use absolute pixel xyxy coordinates.

    Accept a list of {bbox: [x1,y1,x2,y2], score: ...} records. ``polygon`` is
    optional; opaque SAM mask formats must first be decoded by the API adapter.
    """
    image = Image.open(image_path).convert('RGB')
    draw = ImageDraw.Draw(image)
    count = 0
    for count, segment in enumerate(segments, 1):
        color = '#ffed42'
        polygon = segment.get('polygon')
        bbox = segment.get('bbox', segment.get('box', segment.get('xyxy')))
        if polygon:
            points = [tuple(p) for p in polygon]
            draw.line(points + points[:1], fill=color, width=3)
        elif bbox is not None:
            draw.rectangle(tuple(bbox), outline=color, width=3)
        else:
            raise ValueError('segment has neither polygon nor bbox')
        anchor = polygon[0] if polygon else bbox[:2]
        draw.text(tuple(anchor), str(count), fill='black', stroke_fill=color,
                  stroke_width=2, font=_font(15))
    draw.rectangle((0, 0, image.width, 30), fill='#163444')
    draw.text((10, 5), title, fill='white', font=_font(15))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return {'path': str(path), 'segment_count': count, 'label': title}
