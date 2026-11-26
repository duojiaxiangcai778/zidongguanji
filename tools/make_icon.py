#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成「定时关机」图标。

设计：液态玻璃风格的电源符号
  - 深色圆角玻璃底 + 青色径向光晕
  - 青→紫对角渐变画成 C 形环（缺口在正上方）
  - 缺口处一条圆角竖线插向圆心

输出：
  assets/icon.ico          多尺寸（16/24/32/48/64/128/256），小尺寸单独加粗渲染
  assets/icon-256.png      预览用
  assets/icon-512.png      预览用

用法：.venv\\Scripts\\python.exe tools\\make_icon.py
"""

import os
import struct

from PIL import Image, ImageDraw, ImageFilter

# ---------------------------------------------------------------- 基础参数

CANVAS = 1024

C_A = (110, 231, 255)    # #6ee7ff 青
C_B = (167, 139, 250)    # #a78bfa 紫
BG = (11, 16, 23)        # #0b1017 深玻璃底
BG_TOP = (18, 27, 40)    # 顶部略亮

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets")
SIZES = [16, 24, 32, 48, 64, 128, 256]


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def diagonal_gradient(size, c_a, c_b):
    """左上→右下 的对角线性渐变（用等值斜线填充，速度快且够平滑）"""
    grad = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(grad)
    span = size * 2 - 1
    for k in range(0, size * 2, 2):
        t = k / span
        d.line([(k, 0), (0, k)], fill=lerp(c_a, c_b, t), width=4)
    return grad


def vertical_gradient(size, c_top, c_bottom):
    g = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(g)
    for y in range(size):
        d.line([(0, y), (size, y)], fill=lerp(c_top, c_bottom, y / (size - 1)))
    return g


# ---------------------------------------------------------------- 绘制

PRESETS = {
    # ring_w / ring_r / margin 是 1024 画布下的像素，gap 是缺口半角（度）
    "std":  dict(ring_w=90,  gap=21, halo=0.55, gloss=True,  margin=44, ring_r=300),
    "bold": dict(ring_w=112, gap=27, halo=0.34, gloss=True,  margin=44, ring_r=300),
    "tiny": dict(ring_w=130, gap=40, halo=0.0,  gloss=False, margin=18, ring_r=330),
}


def render(canvas=CANVAS, preset="std"):
    """渲染一张 canvas x canvas 的 RGBA 图。
    preset: std(48px 以上) / bold(32px) / tiny(16-24px，更粗更简)"""
    cfg = PRESETS[preset]
    s = canvas / 1024.0

    def px(v):
        return int(round(v * s))

    img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))

    # ---- 1. 玻璃底：圆角矩形 + 垂直渐变 ----
    margin = px(cfg["margin"])
    radius = px(206)
    box = [margin, margin, canvas - margin, canvas - margin]

    base_mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(base_mask).rounded_rectangle(box, radius=radius, fill=255)

    base = vertical_gradient(canvas, BG_TOP, BG)
    img.paste(base, (0, 0), base_mask)

    # ---- 2. 内部光晕（玻璃后面透出的青光）----
    glow_src = Image.new("L", (canvas, canvas), 0)
    gd = ImageDraw.Draw(glow_src)
    r = px(430)
    cx, cy = canvas // 2, int(canvas * 0.44)
    gd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=90)
    glow_alpha = glow_src.filter(ImageFilter.GaussianBlur(px(120)))
    # 圆角裁剪
    glow_alpha = Image.composite(glow_alpha, Image.new("L", (canvas, canvas), 0), base_mask)
    glow_layer = Image.new("RGBA", (canvas, canvas), C_A + (0,))
    glow_layer.putalpha(glow_alpha)
    img = Image.alpha_composite(img, glow_layer)

    # ---- 3. 电源符号的遮罩 ----
    sym = Image.new("L", (canvas, canvas), 0)
    sd = ImageDraw.Draw(sym)

    ring_w = px(cfg["ring_w"])
    ring_r = px(cfg["ring_r"])
    ccx = ccy = canvas // 2
    gap_half = cfg["gap"]

    start = 270 + gap_half
    end = 270 - gap_half + 360
    arc_box = [ccx - ring_r, ccy - ring_r, ccx + ring_r, ccy + ring_r]
    sd.arc(arc_box, start=start, end=end, fill=255, width=ring_w)

    # 弧的两端补圆头（否则断口是直角切面，显得断层）
    import math

    mid_r = ring_r - ring_w / 2
    for ang in (start, end):
        a = math.radians(ang)
        px0 = ccx + mid_r * math.cos(a)
        py0 = ccy + mid_r * math.sin(a)
        sd.ellipse(
            [px0 - ring_w / 2, py0 - ring_w / 2, px0 + ring_w / 2, py0 + ring_w / 2],
            fill=255,
        )

    bar_w = px(cfg["ring_w"])
    bar_top = ccy - ring_r - px(4)
    bar_bottom = ccy - px(78)
    sd.rounded_rectangle(
        [ccx - bar_w // 2, bar_top, ccx + bar_w // 2, bar_bottom],
        radius=bar_w // 2, fill=255,
    )

    # ---- 4. 符号外发光 ----
    if cfg["halo"] > 0:
        halo = sym.filter(ImageFilter.GaussianBlur(px(46)))
        halo = Image.composite(halo, Image.new("L", (canvas, canvas), 0), base_mask)
        halo_layer = Image.new("RGBA", (canvas, canvas), C_A + (0,))
        halo_layer.putalpha(halo.point(lambda v: int(v * cfg["halo"])))
        img = Image.alpha_composite(img, halo_layer)

    # ---- 5. 符号本体：青→紫渐变 ----
    grad = diagonal_gradient(canvas, C_A, C_B)
    img.paste(grad, (0, 0), sym)

    # ---- 6. 顶部高光（玻璃反光，小尺寸不要）----
    if cfg["gloss"]:
        hi = Image.new("L", (canvas, canvas), 0)
        hd = ImageDraw.Draw(hi)
        hd.rounded_rectangle(box, radius=radius, fill=0)
        hd.rounded_rectangle(
            [margin, margin, canvas - margin, margin + px(150)],
            radius=radius, fill=46,
        )
        hi = hi.filter(ImageFilter.GaussianBlur(px(18)))
        hi = Image.composite(hi, Image.new("L", (canvas, canvas), 0), base_mask)
        hi_layer = Image.new("RGBA", (canvas, canvas), (255, 255, 255, 0))
        hi_layer.putalpha(hi)
        img = Image.alpha_composite(img, hi_layer)

    # ---- 7. 外描边（1px 玻璃边缘）----
    edge = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    ed = ImageDraw.Draw(edge)
    ed.rounded_rectangle(box, radius=radius, outline=(255, 255, 255, 38), width=max(1, px(3)))
    img = Image.alpha_composite(img, edge)

    return img


def resize_to(img, size):
    return img.resize((size, size), Image.LANCZOS)


# ---------------------------------------------------------------- ICO 打包

def write_ico(path, images):
    """自己拼 ICO（每张图单独渲染，不靠 Pillow 统一缩放）"""
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)
    entries = b""
    blobs = b""
    offset = 6 + 16 * count

    import io

    for size, im in images:
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        w = 0 if size >= 256 else size
        h = 0 if size >= 256 else size
        entries += struct.pack(
            "<BBBBHHII", w, h, 0, 0, 1, 32, len(data), offset
        )
        offset += len(data)
        blobs += data

    with open(path, "wb") as f:
        f.write(header + entries + blobs)


def main():
    os.makedirs(OUT, exist_ok=True)

    big = render(CANVAS, "std")

    images = []
    for size in SIZES:
        src = render(CANVAS, "tiny") if size <= 32 else big
        images.append((size, resize_to(src, size)))

    ico_path = os.path.join(OUT, "icon.ico")
    write_ico(ico_path, images)

    resize_to(big, 256).save(os.path.join(OUT, "icon-256.png"))
    resize_to(big, 512).save(os.path.join(OUT, "icon-512.png"))

    # 小尺寸预览（放大 8 倍便于肉眼检查像素）
    for size in (16, 24, 32):
        src = render(CANVAS, "tiny") if size <= 32 else big
        src = resize_to(src, size)
        src.resize((size * 8, size * 8), Image.NEAREST).save(
            os.path.join(OUT, f"preview-{size}.png")
        )

    print("生成完成：")
    print("  " + ico_path + f"  ({os.path.getsize(ico_path)} bytes, {len(SIZES)} 个尺寸)")
    print("  " + os.path.join(OUT, "icon-256.png"))
    print("  " + os.path.join(OUT, "icon-512.png"))


if __name__ == "__main__":
    main()
