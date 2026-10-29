#!/usr/bin/env python3
"""Assemble Rose-HiFi.html from src/ (editable source) with embedded assets.

Usage: python3 prototypes/rose-hifi/build.py [output.html]
No network, no dependencies. Output is a single self-contained HTML file.
"""
import base64, hashlib, pathlib, sys, urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent
SRC = ROOT / 'src'

def data_uri(name, mime):
    return 'data:%s;base64,%s' % (mime, base64.b64encode((SRC / 'assets' / name).read_bytes()).decode())

# Horizontal brushed-metal grain: anisotropic turbulence, desaturated.
BRUSH_SVG = ("<svg xmlns='http://www.w3.org/2000/svg' width='220' height='120'>"
             "<filter id='b'><feTurbulence type='fractalNoise' baseFrequency='0.004 0.9' numOctaves='2' seed='7'/>"
             "<feColorMatrix values='0 0 0 0 .5  0 0 0 0 .52  0 0 0 0 .55  0 0 0 .9 0'/></filter>"
             "<rect width='100%' height='100%' filter='url(#b)'/></svg>")

def build():
    html = (SRC / 'index.html').read_text()
    js = {k: (SRC / f).read_text() for k, f in {'MODEL': 'model.js', 'RENDERER': 'renderer.js', 'APP': 'icons.js'}.items()}
    js['APP'] = js['APP'] + '\n' + (SRC / 'views.js').read_text() + '\n' + (SRC / 'app.js').read_text()
    html = html.replace('/*%%CSS%%*/', (SRC / 'style.css').read_text())
    for key, code in js.items():
        html = html.replace('/*%%' + key + '%%*/', code)
    html = html.replace('%%WALLPAPER%%', data_uri('wallpaper.jpg', 'image/jpeg'))
    html = html.replace('%%BRUSH%%', '"data:image/svg+xml,' + urllib.parse.quote(BRUSH_SVG, safe="/:=' ") + '"')
    for name in ('firefox', 'thunderbird', 'vlc'):
        html = html.replace('%%ICON_' + name.upper() + '%%', data_uri(name + '.png', 'image/png'))
    assert '%%' not in html, 'unreplaced placeholder'
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'Rose-HiFi.html'
    out.write_text(html)
    print(out.name, len(html.encode()), 'bytes sha256', hashlib.sha256(html.encode()).hexdigest())

if __name__ == '__main__':
    build()
