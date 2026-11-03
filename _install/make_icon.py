# -*- coding: utf-8 -*-
"""生成应用图标 icon.ico（深色圆角 + 渐变 + 「译」字）"""
import os
from PIL import Image, ImageDraw, ImageFont

SIZE = 512
img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 圆角底：垂直渐变
top = (16, 26, 48)
bot = (36, 74, 168)
grad = Image.new("RGB", (1, SIZE))
for y in range(SIZE):
    t = y / (SIZE - 1)
    grad.putpixel((0, y), tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
grad = grad.resize((SIZE, SIZE))
mask = Image.new("L", (SIZE, SIZE), 0)
ImageDraw.Draw(mask).rounded_rectangle([8, 8, SIZE - 8, SIZE - 8], radius=104, fill=255)
img.paste(grad, (0, 0), mask)

# 高光渐变（左上）
gl = Image.new("L", (SIZE, SIZE), 0)
gd = ImageDraw.Draw(gl)
for i in range(120, 0, -1):
    gd.ellipse([-SIZE * 0.35, -SIZE * 0.55, SIZE * 0.72, SIZE * 0.45], fill=int(9 * (120 - i) / 12))
hl = Image.new("RGBA", (SIZE, SIZE), (140, 180, 255, 0))
hl.putalpha(gl.point(lambda v: min(46, v)))
img = Image.alpha_composite(img, hl)
d = ImageDraw.Draw(img)

# 边框
d.rounded_rectangle([8, 8, SIZE - 8, SIZE - 8], radius=104, outline=(120, 160, 255, 90), width=6)

# 「译」字
font = None
for p in (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc",
          r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\Deng.ttf"):
    if os.path.exists(p):
        try:
            font = ImageFont.truetype(p, 276)
            break
        except Exception:
            continue
if font:
    txt = "译"
    box = d.textbbox((0, 0), txt, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    x = (SIZE - w) / 2 - box[0]
    y = (SIZE - h) / 2 - box[1] - 8
    d.text((x + 3, y + 5), txt, font=font, fill=(6, 12, 28, 150))   # 阴影
    d.text((x, y), txt, font=font, fill=(255, 255, 255, 255))

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
img.save(out, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
print("saved", out, os.path.getsize(out), "bytes")
