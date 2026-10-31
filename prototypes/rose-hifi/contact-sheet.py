#!/usr/bin/env python3
"""Numbered contact sheet for the bottom-tray options (HIFI-TRAY-OPTIONS).

Usage: python3 prototypes/rose-hifi/contact-sheet.py <capture-dir>
Reads home-NN.png and rose-tray-NN.png written by capture-trays.cjs and writes
contact-sheet.png: option 0 (current default) plus options 1-10, each shown as the
Home phone with the populated-Rose tray crop beneath it at the same scale.
Images are composited only (scaled 50%); no pixels are redrawn or retouched.
"""
import json, pathlib, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

NAMES = {
    0: 'Current default (4e843d4)', 1: 'Slim full-width rail', 2: 'Flush inset strip',
    3: 'Floating glass lenses', 4: 'Quiet thin capsule', 5: 'Bottom edge shelf', 6: 'Split wings',
    7: 'Continuous glass', 8: 'Porcelain band', 9: 'Satin fine metal', 10: 'Slender open frame',
}

def font(size, bold=False):
    try:
        name = 'Inter:style=SemiBold' if bold else 'Inter'
        path = subprocess.check_output(['fc-match', '-f', '%{file}', name]).decode()
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

def main(d):
    d = pathlib.Path(d)
    meta = json.loads((d / 'capture-trays.json').read_text())
    opts = sorted({s['option'] for s in meta['shots']})
    scale, cols, gap, head = 0.5, 6, 28, 64
    tiles = []
    for n in opts:
        home = Image.open(d / ('home-%02d.png' % n)).convert('RGB')
        rose = Image.open(d / ('rose-tray-%02d.png' % n)).convert('RGB')
        home = home.resize((int(home.width * scale), int(home.height * scale)), Image.LANCZOS)
        rose = rose.resize((int(rose.width * scale), int(rose.height * scale)), Image.LANCZOS)
        tiles.append((n, home, rose))
    tw = max(t[1].width for t in tiles)
    th = head + tiles[0][1].height + 12 + tiles[0][2].height
    rows = (len(tiles) + cols - 1) // cols
    W, H = cols * tw + (cols + 1) * gap, 110 + rows * th + (rows + 1) * gap + 40
    sheet = Image.new('RGB', (W, H), (238, 242, 246))
    dr = ImageDraw.Draw(sheet)
    dr.text((gap, 28), 'Rose HiFi · bottom-tray options', fill=(20, 25, 34), font=font(34, True))
    dr.text((gap, 72), 'Revision %s · viewport %dx%d @ DPR %d · Home + populated-Rose tray crop at 50%% · headless Chromium/SwiftShader, not phone evidence'
            % (meta['commit'][:12], meta['viewport']['width'], meta['viewport']['height'], meta['deviceScaleFactor']), fill=(80, 90, 110), font=font(17))
    for i, (n, home, rose) in enumerate(tiles):
        x = gap + (i % cols) * (tw + gap)
        y = 110 + gap + (i // cols) * (th + gap)
        label = ('%d' % n) if n else '0'
        dr.ellipse((x, y, x + 40, y + 40), fill=(35, 50, 71) if n else (120, 130, 145))
        dr.text((x + 20, y + 20), label, fill='white', font=font(20, True), anchor='mm')
        dr.text((x + 52, y + 9), NAMES.get(n, ''), fill=(20, 25, 34), font=font(19, True))
        sheet.paste(home, (x, y + head))
        sheet.paste(rose, (x, y + head + home.height + 12))
    out = d / 'contact-sheet.png'
    sheet.save(out, optimize=True)
    print(out, sheet.size)

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'out/trays')
