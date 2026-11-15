#!/usr/bin/env python3
"""Assemble Rose-Keyboard.html (F10 keyboard study) from src/ with embedded assets.

Usage: python3 prototypes/rose-keyboard/build.py [output.html]
No network, no dependencies. Output is a single self-contained HTML file.
"""
import base64, hashlib, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent
SRC = ROOT / 'src'

def build():
    html = (SRC / 'index.html').read_text()
    html = html.replace('/*%%CSS%%*/', (SRC / 'style.css').read_text())
    html = html.replace('/*%%KEYS%%*/', (SRC / 'keyboard.js').read_text())
    html = html.replace('/*%%APP%%*/', (SRC / 'app.js').read_text())
    svg = (SRC / 'assets' / 'field.svg').read_bytes()
    html = html.replace('%%FIELD%%', 'data:image/svg+xml;base64,' + base64.b64encode(svg).decode())
    assert '%%' not in html, 'unreplaced placeholder'
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'Rose-Keyboard.html'
    out.write_text(html)
    print(out.name, len(html.encode()), 'bytes sha256', hashlib.sha256(html.encode()).hexdigest())

if __name__ == '__main__':
    build()
