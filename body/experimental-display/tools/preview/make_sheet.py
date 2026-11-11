#!/usr/bin/env python3
"""Build contact sheets (PNG) from the preview tool's PPMs for both layouts, plus logo / classic / immersive comparisons."""
import os, sys
from PIL import Image, ImageDraw

d = sys.argv[1]
out = sys.argv[2] if len(sys.argv) > 2 else d
logo_path = os.path.expanduser("~/sunday/sunday.png")
names = ["Idle","Happy","Grin","Laughing","Thinking","Listening","Speaking","Surprised","Sleepy","Sleeping"]
states = ["idle","wake","listening","thinking","speaking","sleeping"]
W, H = 320, 240

def sheet(items, prefix, cols, path):
    rows = (len(items) + cols - 1) // cols
    pad, lab = 8, 16
    img = Image.new("RGB", (cols * (W + pad) + pad, rows * (H + lab + pad) + pad), (30, 30, 30))
    dr = ImageDraw.Draw(img)
    for i, n in enumerate(items):
        im = Image.open(os.path.join(d, f"{prefix}_{n}.ppm"))
        x = pad + (i % cols) * (W + pad); y = pad + (i // cols) * (H + lab + pad)
        img.paste(im, (x, y + lab)); dr.text((x, y + 2), n, fill=(255, 255, 255))
    img.save(path)

sheet(names, "expr", 5, os.path.join(out, "expressions_sheet.png"))
sheet(states, "state", 3, os.path.join(out, "states_sheet.png"))
sheet(names, "imm_expr", 5, os.path.join(out, "immersive_expressions_sheet.png"))
sheet(states, "imm_state", 3, os.path.join(out, "immersive_states_sheet.png"))

def triple(name, items):
    pad = 8
    img = Image.new("RGB", (len(items) * (W + pad) + pad, H + 2 * pad + 16), (30, 30, 30)); dr = ImageDraw.Draw(img)
    for i, (label, im) in enumerate(items):
        x = pad + i * (W + pad); img.paste(im, (x, pad + 16)); dr.text((x, pad), label, fill=(255, 255, 255))
    img.save(os.path.join(out, name))

logo = Image.open(logo_path).convert("RGB").resize((240, 240), Image.LANCZOS)
logo_c = Image.new("RGB", (W, H), (30, 30, 30)); logo_c.paste(logo, (40, 0))
ld = lambda p: Image.open(os.path.join(d, p))
triple("logo_vs_idle.png", [("original logo", logo_c), ("classic Idle", ld("expr_Idle.ppm")), ("immersive Idle", ld("imm_expr_Idle.ppm"))])
triple("classic_vs_immersive.png", [(f"{n}: classic | immersive", None) for n in []] or [])
# a classic-vs-immersive grid for the four most telling presets
sel = ["Idle", "Grin", "Surprised", "Sleeping"]
pad = 8
img = Image.new("RGB", (len(sel) * (W + pad) + pad, 2 * (H + pad) + 2 * 16 + pad), (30, 30, 30)); dr = ImageDraw.Draw(img)
for i, n in enumerate(sel):
    x = pad + i * (W + pad)
    dr.text((x, 4), f"classic {n}", fill=(255, 255, 255)); img.paste(ld(f"expr_{n}.ppm"), (x, 20))
    dr.text((x, 20 + H + 4), f"immersive {n}", fill=(255, 255, 255)); img.paste(ld(f"imm_expr_{n}.ppm"), (x, 20 + H + 20))
img.save(os.path.join(out, "classic_vs_immersive.png"))
print("sheets written to", out)
