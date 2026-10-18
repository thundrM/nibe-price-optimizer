"""Icon: hot water drop with a falling price curve – transparent, square."""
import math, sys
from PIL import Image, ImageDraw, ImageFilter

def drop_mask(N):
    m = Image.new("L", (N, N), 0); d = ImageDraw.Draw(m)
    cx, r = N / 2, N * 0.30
    cy = N * 0.62
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    top = N * 0.08
    # tangent points from tip to circle
    dist = cy - top
    a = math.asin(r / dist)
    for s in (-1, 1):
        pass
    tx = r * math.cos(a); ty = cy - r * math.sin(a)
    d.polygon([(cx, top), (cx - tx, ty), (cx + tx, ty)], fill=255)
    return m

def make(N, dark=False):
    big = N * 4
    mask = drop_mask(big)
    grad = Image.new("RGB", (big, big))
    gd = ImageDraw.Draw(grad)
    for y in range(big):
        t = y / big  # top warm orange -> bottom deep red
        c = (int(255 - 40 * t), int(140 - 100 * t), int(40 + 10 * t))
        gd.line([(0, y), (big, y)], fill=c)
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    # falling price curve inside the drop (white), with dots
    pts = [(0.32, 0.55), (0.42, 0.62), (0.52, 0.58), (0.62, 0.72), (0.70, 0.76)]
    P = [(x * big, y * big) for x, y in pts]
    w = int(big * 0.035)
    d.line(P, fill=(255, 255, 255, 255), width=w, joint="curve")
    for x, y in P[::2] + [P[-1]]:
        rr = w * 0.9
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=(255, 255, 255, 255))
    # small arrow head at the end (down-right)
    x, y = P[-1]
    img = img.resize((N, N), Image.LANCZOS)
    return img

out = sys.argv[1]  # e.g. custom_components/nibe_price_optimizer/brand
for name, n in (("icon.png", 256), ("icon@2x.png", 512)):
    make(n).save(f"{out}/{name}")
print("ok")
