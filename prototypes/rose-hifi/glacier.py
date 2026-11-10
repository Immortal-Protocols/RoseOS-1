#!/usr/bin/env python3
"""Glacier palette variant (GLACIER-1E-1110) — derived at build time from the Oxblood layer.

Oxblood's stylesheet is never edited. Each Oxblood rule is replayed in full, in order, with its selector scoped to
`#device[data-palette=glacier]` and only its colours remapped (warm burgundy/ruby → mineral blue /
blue graphite; warm ivory ink → sunlit silver). Geometry, icons and interaction logic are shared.
Rules that carry warning, destructive or microphone-muted meaning keep their warm colours, so those
states stay distinguishable from blue selection.

PALETTE below is PROVISIONAL until the Illustrator's GLACIER-1E-1110 palette lands; final values
replace these anchors without touching the mapping code.
"""
import colorsys, re

# ---- Provisional palette anchors (replace with Illustrator's final semantic palette) ----
PALETTE = {
    'status': 'provisional',          # becomes 'final' with the Illustrator's palette
    'hue': 211,                       # mineral blue (deg); avoids neon cyan (~185–195)
    'neutral_hue': 214,               # blue graphite / sunlit silver neutrals
    'chroma_cap': 0.52,               # saturation ceiling for chromatic colours
    'chroma_scale': 0.72,             # saturation scale for chromatic colours
    'neutral_scale': 0.85,            # saturation scale for low-chroma (ink/hairline) colours
    'dark_lift': 0.018,               # lightness lift for very dark recesses (graphite, not black)
}

# Selectors whose colours keep their Oxblood (warm) meaning in Glacier.
KEEP_WARM = re.compile(r'muted|stop|danger|destructive|attention|error|fail', re.I)
SCOPE = '#device[data-theme=oxblood]'
GLACIER_SCOPE = '#device[data-theme=oxblood][data-palette=glacier]'


def _map_rgb(r, g, b):
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    hd = h * 360
    warm = hd >= 300 or hd <= 55
    if s < 0.02 or not warm:            # greys, black, white and already-cool colours stay
        return r, g, b
    P = PALETTE
    if s > 0.35 and l < 0.92:           # chromatic: burgundy / ruby
        s2 = min(s * P['chroma_scale'], P['chroma_cap']); h2 = P['hue']
    else:                               # warm neutrals: ivory ink, warm hairlines
        s2 = s * P['neutral_scale']; h2 = P['neutral_hue']
    if l < 0.12:
        l = min(0.12, l + P['dark_lift'])
    rr, gg, bb = colorsys.hls_to_rgb(h2 / 360, l, s2)
    return round(rr * 255), round(gg * 255), round(bb * 255)


def map_colors(text):
    def hx(m):
        v = m.group(1)
        if len(v) in (3, 4):
            v = ''.join(c * 2 for c in v)
        r, g, b = int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
        a = v[6:8] if len(v) == 8 else ''
        r, g, b = _map_rgb(r, g, b)
        return '#%02x%02x%02x%s' % (r, g, b, a)
    text = re.sub(r'#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b(?![\w-])', hx, text)

    def rgb(m):
        r, g, b = _map_rgb(int(m.group(2)), int(m.group(3)), int(m.group(4)))
        return '%s(%d,%d,%d' % (m.group(1), r, g, b)
    return re.sub(r'(rgba?)\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', rgb, text)


COLOR_RE = re.compile(r'#[0-9a-fA-F]{3,8}\b|rgba?\(')


def derive_css(oxblood_css):
    """Replay every Oxblood rule in the Glacier scope with remapped colours."""
    css = re.sub(r'/\*.*?\*/', '', oxblood_css, flags=re.S)
    out, renamed = [], {}
    # Keyframes with colours: renamed Glacier copies.
    for m in re.finditer(r'@keyframes\s+([\w-]+)\s*\{((?:[^{}]*\{[^{}]*\})*)\s*\}', css):
        if COLOR_RE.search(m.group(2)):
            renamed[m.group(1)] = 'glacier-' + m.group(1)
            out.append('@keyframes glacier-%s{%s}' % (m.group(1), map_colors(m.group(2))))
    css = re.sub(r'@keyframes\s+[\w-]+\s*\{(?:[^{}]*\{[^{}]*\})*\s*\}', '', css)
    css = re.sub(r'@property[^{]+\{[^{}]*\}', '', css)

    def emit(sel, body):
        # Every Oxblood rule is replayed in full and in order, so shorthands/longhands cascade exactly
        # as in Oxblood; only colours change. Warm-meaning rules are replayed with their colours kept.
        if SCOPE not in sel:
            return None
        body2 = body if KEEP_WARM.search(sel) else map_colors(body)
        for a, b in renamed.items():
            body2 = re.sub(r'\b%s\b' % re.escape(a), b, body2)
        return sel.replace(SCOPE, GLACIER_SCOPE).strip() + '{' + body2 + '}'

    # Top-level rules and rules inside @media blocks.
    pos = 0
    for m in re.finditer(r'@media([^{]+)\{((?:[^{}]*\{[^{}]*\})*)\s*\}|([^{}@]+)\{([^{}]*)\}', css):
        if m.group(1):
            inner = [emit(s, b) for s, b in re.findall(r'([^{}]+)\{([^{}]*)\}', m.group(2))]
            inner = [x for x in inner if x]
            if inner:
                out.append('@media' + m.group(1) + '{' + ''.join(inner) + '}')
        else:
            r = emit(m.group(3), m.group(4))
            if r:
                out.append(r)
    return '/* Glacier (derived from Oxblood at build time; palette %s) */\n' % PALETTE['status'] + '\n'.join(out)


def derive_svg(svg_text):
    """Provisional Glacier field: the Oxblood field geometry with the Glacier colour mapping."""
    return map_colors(svg_text)
