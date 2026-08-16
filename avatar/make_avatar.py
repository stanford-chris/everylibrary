#!/usr/bin/env python3
"""Generate avatar variants for the everylibrary bot.

The mark is a library date stamp: the rubber-stamped impression on the return
slip inside the front cover of a British library book. It suits an avatar
because the artefact is already a circle, so it fills the circular crop instead
of fighting it, and because it says 'library' specifically rather than 'books'.

Gill Sans throughout: the British institutional face, on Penguin, the BBC,
British Rail and a great many library signs.
"""

import math
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops

SS = 3
OUT = 1024
W = OUT * SS

INK = (90, 58, 126)         # library date-stamp violet
PAPER = (237, 237, 230)     # catalogue-card stock

GILL = '/System/Library/Fonts/Supplemental/GillSans.ttc'


def font(size, index=1):     # index 1 = Gill Sans Bold within the .ttc
    return ImageFont.truetype(GILL, size, index=index)


def arc_text(layer, text, cx, cy, radius, fnt, mid_deg, flip=False, tracking=1.0):
    """Set text around a circle, one rotated glyph at a time: PIL has no
    text-on-a-path.

    Screen angles run 0=right, 90=bottom, 180=left, 270=top. Along the top,
    reading left to right means increasing angle; along the bottom it means
    DEcreasing angle. So bottom text only needs the walk direction negated.
    Reversing the string as well double-negates and prints 'MODGNIK DETINU'.
    """
    widths = [(fnt.getbbox(ch)[2] - fnt.getbbox(ch)[0]) + fnt.size * 0.13
              for ch in text]
    total_deg = math.degrees(sum(widths) * tracking / radius)
    direction = -1 if flip else 1
    cursor = mid_deg - direction * total_deg / 2

    for ch, w in zip(text, widths):
        step = math.degrees(w * tracking / radius)
        ang = cursor + direction * step / 2
        cursor += direction * step
        if ch == ' ':
            continue

        pad = int(fnt.size * 0.95)
        glyph = Image.new('L', (pad * 2, pad * 2), 0)
        ImageDraw.Draw(glyph).text((pad, pad), ch, font=fnt, fill=255, anchor='mm')
        glyph = glyph.rotate(-ang + (90 if flip else -90),
                             resample=Image.BICUBIC, expand=False)

        rad = math.radians(ang)
        layer.paste(255, (int(cx + radius * math.cos(rad)) - pad,
                          int(cy + radius * math.sin(rad)) - pad), glyph)


def facade(layer, cx, cy, size):
    """A civic library front reduced to a pediment over columns. It must hold up
    at 40px in a feed: five heavy verticals, one triangle, one plinth.

    Every horizontal is derived from `span` so the roof is always wider than the
    columns it sits on.
    """
    d = ImageDraw.Draw(layer)
    span = size * 1.80
    half = span / 2

    ent_top = cy - size * 0.10          # top of the entablature
    ent_h = size * 0.15
    col_top = ent_top + ent_h
    col_bot = cy + size * 0.62

    # pediment
    d.polygon([(cx - half, ent_top), (cx + half, ent_top),
               (cx, ent_top - size * 0.62)], fill=255)
    # entablature
    d.rectangle([cx - half, ent_top, cx + half, col_top], fill=255)

    # columns, spanning 80% of the roof so they sit under it
    n, inset = 5, span * 0.80
    col_w = inset * 0.132
    gap = (inset - n * col_w) / (n - 1)
    x = cx - inset / 2
    for _ in range(n):
        d.rectangle([x, col_top + size * 0.06, x + col_w, col_bot], fill=255)
        x += col_w + gap

    # plinth, wider than everything above it
    d.rectangle([cx - half * 1.07, col_bot,
                 cx + half * 1.07, col_bot + size * 0.17], fill=255)


def stamp_mark(centre='facade', ring_text=True):
    layer = Image.new('L', (W, W), 0)
    d = ImageDraw.Draw(layer)
    c = W / 2

    if ring_text:
        outer, inner = W * 0.455, W * 0.372
        d.ellipse([c - outer, c - outer, c + outer, c + outer],
                  outline=255, width=int(W * 0.020))
        d.ellipse([c - inner, c - inner, c + inner, c + inner],
                  outline=255, width=int(W * 0.009))

        f = font(int(W * 0.078))
        arc_text(layer, 'EVERY LIBRARY', c, c, W * 0.414, f, 270, tracking=1.14)
        arc_text(layer, 'UNITED KINGDOM', c, c, W * 0.414, f, 90, flip=True,
                 tracking=1.14)

        for deg in (0, 180):
            r = math.radians(deg)
            x, y = c + W * 0.414 * math.cos(r), c + W * 0.414 * math.sin(r)
            d.ellipse([x - W * 0.017, y - W * 0.017,
                       x + W * 0.017, y + W * 0.017], fill=255)

    if centre == 'facade':
        facade(layer, c, c, W * (0.155 if ring_text else 0.235))
    elif centre == 'count':
        f = font(int(W * (0.185 if ring_text else 0.30)))
        d.text((c, c), '3,750', font=f, fill=255, anchor='mm')

    return layer


def ink_texture(mask):
    """Rubber never prints evenly. Two scales of noise: a fine mottle that keeps
    the ink alive, and coarser patches that lift it off the paper in places.

    The first version multiplied two mid-grey noise fields together and clamped,
    which saturated to 255 everywhere and produced a perfectly flat mark.
    """
    fine = Image.effect_noise((W, W), 52).filter(ImageFilter.GaussianBlur(W * 0.0035))
    fine = fine.point(lambda v: max(216, min(255, int(240 + (v - 128) * 0.85))))

    coarse = Image.effect_noise((W, W), 60).filter(ImageFilter.GaussianBlur(W * 0.018))
    coarse = coarse.point(lambda v: max(178, min(255, int(238 + (v - 128) * 2.6))))

    out = ImageChops.multiply(mask, fine)
    return ImageChops.multiply(out, coarse)


def paper_ground():
    base = Image.new('RGB', (W, W), PAPER)
    grain = Image.effect_noise((W, W), 8).filter(ImageFilter.GaussianBlur(W * 0.0012))
    return Image.blend(base, Image.merge('RGB', (grain, grain, grain)), 0.04)


def render(name, centre, ring_text, rotate=-3.0):
    mask = ink_texture(stamp_mark(centre, ring_text))
    mask = mask.rotate(rotate, resample=Image.BICUBIC, fillcolor=0)

    img = paper_ground()
    img.paste(Image.new('RGB', (W, W), INK), (0, 0), mask)
    img = img.resize((OUT, OUT), Image.LANCZOS)
    img.save(name, 'PNG', optimize=True)

    img.resize((44, 44), Image.LANCZOS).resize((176, 176), Image.NEAREST) \
       .save(name.replace('.png', '_at44px.png'), 'PNG')

    stats = mask.resize((256, 256), Image.LANCZOS).getdata()
    inked = [v for v in stats if v > 20]
    print(f'  {name:34} ink pixels {len(inked):>6}  '
          f'mean alpha {sum(inked) / max(len(inked), 1):.0f}/255')


if __name__ == '__main__':
    render('avatar_a_stamp_facade.png', 'facade', True)
    render('avatar_b_stamp_count.png', 'count', True)
    render('avatar_c_facade_plain.png', 'facade', False, rotate=-2.0)
