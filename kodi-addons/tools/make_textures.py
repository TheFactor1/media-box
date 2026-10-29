"""Generates skin.mediabox/media/mediabox/*.png (stars, focus glow, poster mask, fades). Standard library only.
Run once after changing a shape: python kodi-addons/tools/make_textures.py — the PNGs are committed."""
import math
import os
import random
import struct
import zlib

OUT = os.path.join(os.path.dirname(__file__), '..', 'skin.mediabox', 'media', 'mediabox')


def png(path, w, h, px):
    """px(x, y) -> (r, g, b, a) 0..255. RGBA, 8 bit."""
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    raw = b''.join(b'\x00' + bytes(c for x in range(w) for c in px(x, y)) for y in range(h))
    with open(os.path.join(OUT, path), 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def rrect_dist(x, y, x0, y0, x1, y1, r):
    """Signed distance from pixel centre to a rounded rectangle (negative inside)."""
    cx, cy = min(max(x + .5, x0 + r), x1 - r), min(max(y + .5, y0 + r), y1 - r)
    return math.hypot(x + .5 - cx, y + .5 - cy) - r


def clamp(v):
    return max(0, min(255, int(round(v))))


def main():
    os.makedirs(OUT, exist_ok=True)
    rnd = random.Random(7)  # fixed seed: same sky every build

    # Faint star field, 1920x1080, mostly transparent so it compresses to almost nothing.
    stars = {}
    for _ in range(420):
        x, y = rnd.randrange(1920), rnd.randrange(1080)
        stars[(x, y)] = rnd.choice((40, 60, 80, 110, 150, 200))
    for _ in range(14):  # a few brighter ones with a 1 px halo
        x, y = rnd.randrange(1, 1919), rnd.randrange(1, 1079)
        stars[(x, y)] = 235
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            stars.setdefault((x + dx, y + dy), 70)
    png('stars.png', 1920, 1080, lambda x, y: (235, 242, 255, stars[(x, y)]) if (x, y) in stars else (0, 0, 0, 0))

    # Focus glow: 9-slice (border 56). Soft #9fd8ff halo outside a 1.5 px light line; inside transparent.
    S, I, R = 160, 40, 16
    def glow(x, y):
        d = rrect_dist(x, y, I, I, S - I, S - I, R)
        line = max(0.0, 1 - abs(d + 0.75) / 1.25) * 0.82
        halo = 0.0 if d < 0 else 0.45 * math.exp(-(d / 15) ** 2)
        a = max(line, halo)
        mix = line / a if a else 0  # line is near-white, halo is accent blue
        return (clamp(159 + (235 - 159) * mix), clamp(216 + (245 - 216) * mix), 255, clamp(a * 255))
    png('glow.png', S, S, glow)

    # Poster corner mask for <texture diffuse>: white rounded rect, sized like a poster so corners stay round.
    png('poster-mask.png', 240, 360, lambda x, y: (255, 255, 255, clamp(255 * min(1, max(0, 0.5 - rrect_dist(x, y, 0, 0, 240, 360, 14))))))
    png('dot.png', 32, 32, lambda x, y: (255, 255, 255, clamp(255 * min(1, max(0, 15.5 - math.hypot(x + .5 - 16, y + .5 - 16))))))
    # Tab underline: 2 px line 4 px above the band's bottom edge, with a soft glow (drawn at the button's 84 px height).
    png('underline.png', 16, 84, lambda x, y: (255, 255, 255, clamp(255 if y in (78, 79) else 90 * math.exp(-((y - 78.5) / 2.2) ** 2))))
    png('white.png', 8, 8, lambda x, y: (255, 255, 255, 255))
    # Backdrop alpha mask (<texture diffuse>, stretched to 1300x760): fades in from the left, out towards the
    # bottom and slightly at the top, so the picture melts into the sky instead of sitting in a box.
    def backdrop(x, y):
        fx, fy = x / 259, y / 151
        a = min(1, fx / 0.45) ** 1.6 * min(1, (1 - fy) / 0.5) ** 1.4 * min(1, fy / 0.12 + 0.35)
        return (255, 255, 255, clamp(255 * a))
    png('backdrop-mask.png', 260, 152, backdrop)
    # Deep-blue wash for the page: #05070f at the top to a deep blue at the bottom.
    png('sky.png', 4, 256, lambda x, y: (clamp(5 + 3 * y / 255), clamp(7 + 13 * y / 255), clamp(15 + 37 * y / 255), 255))


if __name__ == '__main__':
    main()
    for f in sorted(os.listdir(OUT)):
        print(f, os.path.getsize(os.path.join(OUT, f)))
