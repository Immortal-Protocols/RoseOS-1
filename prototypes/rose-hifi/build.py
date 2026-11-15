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
BRUSH_SVG = ("<svg xmlns='http://www.w3.org/2000/svg' width='640' height='160'>"
             "<filter id='b' x='0' y='0' width='100%' height='100%'>"
             "<feTurbulence type='fractalNoise' baseFrequency='0.0015 1.1' numOctaves='2' seed='7' stitchTiles='stitch' result='n'/>"
             "<feTurbulence type='fractalNoise' baseFrequency='0.004 0.03' numOctaves='1' seed='3' stitchTiles='stitch' result='w'/>"
             "<feDisplacementMap in='n' in2='w' scale='4' xChannelSelector='R' yChannelSelector='G'/>"
             "<feColorMatrix values='1.3 0 0 0 -.15  1.3 0 0 0 -.15  1.3 0 0 0 -.15  0 0 0 0 1'/></filter>"
             "<rect width='100%' height='100%' filter='url(#b)'/></svg>")

import glacier

def glacier_field():
    # Final Illustrator artwork (GLACIER-1E-1110-ILL-DELIVERY-v1): glacier-portrait.png, embedded unchanged.
    f = SRC / 'assets' / 'glacier' / 'field.png'
    if f.exists():
        return 'data:image/png;base64,' + base64.b64encode(f.read_bytes()).decode()
    svg = glacier.derive_svg((SRC / 'assets' / 'oxblood-home.svg').read_text())   # provisional fallback
    return 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode()).decode()

def build():
    html = (SRC / 'index.html').read_text()
    js = {k: (SRC / f).read_text() for k, f in {'MODEL': 'model.js', 'RENDERER': 'renderer.js', 'APP': 'icons.js'}.items()}
    # The F10 keyboard text model is consumed read-only from the keyboard study (818a032 source).
    keys = (ROOT.parent / 'rose-keyboard' / 'src' / 'keyboard.js').read_text()
    js['APP'] = js['APP'] + '\n' + (SRC / 'views.js').read_text() + '\n' + (SRC / 'app.js').read_text() + '\n' + keys + '\n' + (SRC / 'system.js').read_text() + '\n' + (SRC / 'workspace.js').read_text()
    # Tray options are opt-in (data-tray on #screen) and appended after the default styles.
    ox = (SRC / 'oxblood.css').read_text()
    html = html.replace('/*%%CSS%%*/', (SRC / 'style.css').read_text() + '\n' + (SRC / 'tray-variants.css').read_text() + '\n' + ox +
                        '\n' + glacier.derive_css(ox) + '\n' + (SRC / 'glacier.css').read_text() + '\n' + (SRC / 'system.css').read_text() + '\n' + (SRC / 'workspace.css').read_text())
    for key, code in js.items():
        html = html.replace('/*%%' + key + '%%*/', code)
    html = html.replace('%%WALLPAPER%%', data_uri('wallpaper.jpg', 'image/jpeg'))
    html = html.replace('%%OX_HOME%%', data_uri('oxblood-home.svg', 'image/svg+xml'))
    html = html.replace('%%OX_ROSE%%', data_uri('oxblood-rose.svg', 'image/svg+xml'))
    html = html.replace('%%GL_FIELD%%', glacier_field())
    html = html.replace('%%BRUSH%%', '"data:image/svg+xml,' + urllib.parse.quote(BRUSH_SVG, safe="/:=' ") + '"')
    for name in ('firefox', 'thunderbird', 'vlc'):
        html = html.replace('%%ICON_' + name.upper() + '%%', data_uri(name + '.png', 'image/png'))
    assert '%%' not in html, 'unreplaced placeholder'
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'Rose-HiFi.html'
    out.write_text(html)
    print(out.name, len(html.encode()), 'bytes sha256', hashlib.sha256(html.encode()).hexdigest())
    # Glacier review launch file: same source, Glacier palette by default (Oxblood stays in the harness).
    gl = html.replace('<script>', '<script>window.ROSE_DEFAULT_THEME="glacier";</script><script>', 1)
    gout = out.with_name('Rose-Glacier.html')
    gout.write_text(gl)
    print(gout.name, len(gl.encode()), 'bytes sha256', hashlib.sha256(gl.encode()).hexdigest())

if __name__ == '__main__':
    build()
