#!/usr/bin/env python3
"""Paginated tray-option comparison pages for small viewers (HIFI-TRAY-OPTIONS export).

Usage: python3 prototypes/rose-hifi/tray-pages.py <packet-dir>
Reads the existing tray crops (<packet-dir>/tray-crops/*.png, written by tray-sheet.py from
the tested renders) and capture-trays.json, and writes <packet-dir>/pages/:
  tray-options-1-4.png, tray-options-5-8.png, tray-options-9-10.png
Each page is a matrix: one row per option (number, name, material note), columns
Home · voice on | Home · voice muted | Rose · call controls, all crops at one scale.
Pages are 2200 px wide (2x) so they read at ~1100x700 in a landscape browser.
Crops are only scaled; nothing is re-rendered or retouched.
"""
import hashlib, json, pathlib, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

OPTIONS = {
    1: ('Slim full-width rail', 'Satin metal rail, edge to edge;', 'soft pill controls'),
    2: ('Flush inset strip', 'Sunken translucent band on the', 'bottom edge; no frame'),
    3: ('Floating glass lenses', 'No bar; three separate', 'clear glass lenses'),
    4: ('Quiet thin capsule', 'Frosted low-contrast capsule;', 'glyph-only controls'),
    5: ('Bottom edge shelf', 'Satin shelf grown from the bottom', 'edge, rounded on top'),
    6: ('Split wings', 'Satin wings on the side edges;', 'open middle'),
    7: ('Continuous glass', 'One clear blurred slab;', 'hairline-separated segments'),
    8: ('Porcelain band', 'Opaque warm ivory porcelain', 'with soft gloss'),
    9: ('Satin fine metal', 'Slim smooth metal, no brushing;', 'small clear lenses'),
    10: ('Slender open frame', 'Hairline frame, open interior;', 'light glass pills'),
}
PAGES = [(1, 2, 3, 4), (5, 6, 7, 8), (9, 10)]
COLS = [('tray-home', "Home · Rose's voice on"), ('tray-home-voice-muted', "Home · Rose's voice muted"), ('tray-rose', 'Rose · call controls (mic · end · apps)')]

def font(size, bold=False):
    try:
        path = subprocess.check_output(['fc-match', '-f', '%{file}', 'Inter:style=SemiBold' if bold else 'Inter']).decode()
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

def main(d):
    d = pathlib.Path(d)
    meta = json.loads((d / 'capture-trays.json').read_text())
    out = d / 'pages'
    out.mkdir(exist_ok=True)
    W, M, LABEL, GAP = 2200, 48, 440, 24
    cw = (W - 2 * M - LABEL - 2 * GAP) // 3
    ch = round(cw * 240 / 816)
    row = ch + 36
    top = 236
    for opts in PAGES:
        H = top + len(PAGES[0]) * row + 96
        page = Image.new('RGB', (W, H), (240, 243, 247))
        dr = ImageDraw.Draw(page)
        dr.text((M, 34), 'Rose HiFi · bottom-tray options %d–%d' % (opts[0], opts[-1]), fill=(20, 25, 34), font=font(46, True))
        dr.text((M, 98), 'Prompts, not a selection · options 1–4, 5–8 and 9–10 share one crop scale (%d%%)' % round(100 * cw / 816), fill=(70, 80, 100), font=font(26))
        for j, (_, title) in enumerate(COLS):
            x = M + LABEL + j * (cw + GAP)
            dr.text((x, top - 58), title, fill=(40, 50, 70), font=font(27, True))
        for i, n in enumerate(opts):
            y = top + i * row
            dr.ellipse((M, y + 8, M + 60, y + 68), fill=(35, 50, 71))
            dr.text((M + 30, y + 38), str(n), fill='white', font=font(32, True), anchor='mm')
            dr.text((M + 78, y + 6), OPTIONS[n][0], fill=(20, 25, 34), font=font(33, True))
            dr.text((M + 78, y + 54), OPTIONS[n][1], fill=(70, 80, 100), font=font(23))
            dr.text((M + 78, y + 84), OPTIONS[n][2], fill=(70, 80, 100), font=font(23))
            for j, (stem, _) in enumerate(COLS):
                im = Image.open(d / 'tray-crops' / ('%s-%02d.png' % (stem, n))).convert('RGB')
                page.paste(im.resize((cw, ch), Image.LANCZOS), (M + LABEL + j * (cw + GAP), y))
        foot = H - 64
        dr.text((M, foot), 'Revision %s · bottom 120 CSS px of tested renders (tray-crops/) · 1280x900 @ DPR 2 · headless Chromium/SwiftShader, not phone evidence'
                % meta['commit'], fill=(90, 100, 118), font=font(21))
        name = 'tray-options-%d-%d.png' % (opts[0], opts[-1])
        page.save(out / name, optimize=True)
        print(name, page.size, hashlib.sha256((out / name).read_bytes()).hexdigest())

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'out/packet')
