#!/usr/bin/env python3
"""Readable, tray-focused comparison for the bottom-tray options (HIFI-TRAY-OPTIONS).

Usage: python3 prototypes/rose-hifi/tray-sheet.py <capture-dir>
Input: files written by capture-trays.cjs (and capture.cjs into <capture-dir>/context).
Output in <capture-dir>:
  tray-crops/   the same bottom band (120 CSS px = 240 px at DPR 2, full phone width) cut
                from each tested render: tray-home-NN, tray-home-voice-muted-NN, tray-rose-NN
  tray-comparison.png   options 1-10 in 2 columns x 5 rows; each shows Home (voice on),
                Home (Rose's voice muted) and Rose's call controls at one scale (75%)
  context.png   full-screen current default Home and populated Rose, with the cropped band outlined
Pixels are only cropped and scaled; nothing is redrawn or retouched.
"""
import json, pathlib, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

BAND = 240  # px at DPR 2 = 120 CSS px from the bottom of the phone (incl. bezel)
OPTIONS = {
    1: ('Slim full-width rail', 'Satin metal rail, edge to edge; soft pill controls'),
    2: ('Flush inset strip', 'Sunken translucent band on the bottom edge; no frame'),
    3: ('Floating glass lenses', 'No bar; three separate clear glass lenses'),
    4: ('Quiet thin capsule', 'Frosted low-contrast capsule; glyph-only controls'),
    5: ('Bottom edge shelf', 'Satin shelf grown from the bottom edge, rounded on top'),
    6: ('Split wings', 'Satin wings on the side edges; open middle'),
    7: ('Continuous glass', 'One clear blurred slab; hairline-separated segments'),
    8: ('Porcelain band', 'Opaque warm ivory porcelain with soft gloss'),
    9: ('Satin fine metal', 'Slim smooth metal, no brushing; small clear lenses'),
    10: ('Slender open frame', 'Hairline frame, open interior; light glass pills'),
}

def font(size, bold=False):
    try:
        path = subprocess.check_output(['fc-match', '-f', '%{file}', 'Inter:style=SemiBold' if bold else 'Inter']).decode()
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

def band(img):
    return img.crop((0, img.height - BAND, img.width, img.height))

def main(d):
    d = pathlib.Path(d)
    meta = json.loads((d / 'capture-trays.json').read_text())
    crops = d / 'tray-crops'
    crops.mkdir(exist_ok=True)
    cells = {}
    for n in OPTIONS:
        trio = [
            ("Home · Rose's voice on", band(Image.open(d / ('home-%02d.png' % n)).convert('RGB'))),
            ("Home · Rose's voice muted", band(Image.open(d / ('home-voice-muted-%02d.png' % n)).convert('RGB'))),
            ('Rose · call controls (mic · end · apps)', band(Image.open(d / ('rose-tray-%02d.png' % n)).convert('RGB'))),
        ]
        for name, (_, im) in zip(('tray-home', 'tray-home-voice-muted', 'tray-rose'), trio):
            im.save(crops / ('%s-%02d.png' % (name, n)), optimize=True)
        cells[n] = trio

    scale, gap, cols = 0.75, 36, 2
    cw = int(816 * scale); ch = int(BAND * scale)
    head, cap = 74, 30
    cell_h = head + 3 * (cap + ch + 10)
    W = cols * cw + (cols + 1) * gap
    H = 150 + 5 * cell_h + 6 * gap
    sheet = Image.new('RGB', (W, H), (240, 243, 247))
    dr = ImageDraw.Draw(sheet)
    dr.text((gap, 30), 'Rose HiFi · bottom-tray options 1–10', fill=(20, 25, 34), font=font(36, True))
    dr.text((gap, 80), 'Revision %s · tray band = bottom 120 CSS px · all crops at the same 75%% scale' % meta['commit'][:12], fill=(70, 80, 100), font=font(19))
    dr.text((gap, 108), 'Prompts, not a selection · headless Chromium/SwiftShader renders, not phone evidence', fill=(70, 80, 100), font=font(19))
    for i, n in enumerate(OPTIONS):
        x = gap + (i % cols) * (cw + gap)
        y = 150 + gap + (i // cols) * (cell_h + gap)
        dr.ellipse((x, y, x + 46, y + 46), fill=(35, 50, 71))
        dr.text((x + 23, y + 23), str(n), fill='white', font=font(24, True), anchor='mm')
        dr.text((x + 60, y + 2), OPTIONS[n][0], fill=(20, 25, 34), font=font(25, True))
        dr.text((x + 60, y + 36), OPTIONS[n][1], fill=(80, 90, 110), font=font(17))
        yy = y + head
        for label, im in cells[n]:
            dr.text((x, yy + 4), label, fill=(60, 70, 90), font=font(17))
            sheet.paste(im.resize((cw, ch), Image.LANCZOS), (x, yy + cap))
            yy += cap + ch + 10
    sheet.save(d / 'tray-comparison.png', optimize=True)

    ctx = d / 'context'
    if (ctx / 'home.png').exists() and (ctx / 'rose.png').exists():
        cmeta = json.loads((ctx / 'capture.json').read_text())
        home = Image.open(ctx / 'home.png').convert('RGB'); rose = Image.open(ctx / 'rose.png').convert('RGB')
        s = 0.6
        hw, hh = int(home.width * s), int(home.height * s)
        CW, CH = 2 * hw + 3 * gap, hh + 200
        c = Image.new('RGB', (CW, CH), (240, 243, 247))
        cd = ImageDraw.Draw(c)
        cd.text((gap, 26), 'Context · current default (not a selection)', fill=(20, 25, 34), font=font(30, True))
        cd.text((gap, 70), 'Revision %s · outlined band = region shown in the tray crops' % cmeta['commit'][:12], fill=(70, 80, 100), font=font(18))
        for j, (label, im) in enumerate((('Home', home), ('Rose · populated', rose))):
            x = gap + j * (hw + gap); y = 150
            cd.text((x, y - 36), label, fill=(60, 70, 90), font=font(20, True))
            c.paste(im.resize((hw, hh), Image.LANCZOS), (x, y))
            top = y + int((im.height - BAND) * s)
            cd.rectangle((x - 3, top, x + hw + 2, y + hh + 2), outline=(220, 60, 90), width=3)
        c.save(d / 'context.png', optimize=True)
    print('tray-comparison.png', sheet.size, '· crops', len(list(crops.glob('*.png'))))

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'out/trays')
