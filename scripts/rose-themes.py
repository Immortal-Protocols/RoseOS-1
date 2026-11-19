#!/usr/bin/env python3
"""Rose themes (plan 1E): one shared definition, one generated RoseTheme.java per package.

themes/rose-themes.json is the only place a theme is defined: its stable id, label, the words people say for it,
how its artwork colours derive from Oxblood's, its own wallpapers and the semantic colours of each system surface.
Rose Agent, SystemUI/WMShell, Launcher3 quickstep and the Rose keyboard are built separately and cannot share a
class, so this script writes the same pure RoseTheme.java into each of them; --check (the test) fails when any copy
is stale. A new theme is a new entry in the JSON plus its assets, then `python3 scripts/rose-themes.py --write`.

Glacier's artwork derivation is the approved prototype's colour map (themes/reference/glacier.py, copied unchanged
from prototype revision 2ecceb97, SHA-256 pinned below), ported to Java. --write also records golden values of that
map for every colour the native renderers use, so the Java port is checked against the prototype itself.
"""
import argparse
import hashlib
import importlib.util
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
THEMES = ROOT / 'themes'
DEFINITION = THEMES / 'rose-themes.json'
REFERENCE = THEMES / 'reference' / 'glacier.py'
REFERENCE_SHA256 = 'd26f04697345609649903c4a7dc2aac25523a565d569b0068b4e7289027f8a62'
GOLDEN = THEMES / 'reference' / 'glacier-map-golden.txt'

TARGETS = {
    'app': ('workspace/rose-agent/app/src/dev/pixel/voice/RoseTheme.java', 'dev.pixel.voice'),
    'shell': ('os/overlays/frameworks/base/libs/WindowManager/Shell/src/com/android/wm/shell/RoseTheme.java',
              'com.android.wm.shell'),
    'launcher3': ('os/overlays/packages/apps/Launcher3/quickstep/src/com/android/quickstep/fallback/RoseTheme.java',
                  'com.android.quickstep.fallback'),
    'keyboard': ('os/overlays/packages/apps/RoseKeyboard/src/dev/pixel/rose/keyboard/RoseTheme.java',
                 'dev.pixel.rose.keyboard'),
}

# Raster wallpapers are copied byte for byte, as their theme's named drawable, into every package that draws them
# (checked like the Java copies): Rose Agent (Home, Rose) and Launcher3 (the switcher field).
RASTER_DIRS = ['workspace/rose-agent/app/res/drawable-nodpi',
               'os/overlays/packages/apps/Launcher3/quickstep/res/drawable-nodpi']
DRAWABLE = re.compile(r'^rose_[a-z0-9_]+$')


def asset_copies(d):
    """{theme asset (relative to themes/): [package copies]} for every raster theme."""
    return {t['wallpapers']['asset']: ['%s/%s.png' % (r, t['wallpapers']['drawable']) for r in RASTER_DIRS]
            for t in d['themes'] if t['wallpapers'].get('kind') == 'raster'}

# Every colour literal the native renderers draw with is a golden input; these sources are scanned.
GOLDEN_SOURCES = [
    'workspace/rose-agent/app/src/dev/pixel/voice',
    'os/overlays/packages/apps/Launcher3/quickstep/src/com/android/quickstep/fallback',
    'os/overlays/frameworks/base/libs/WindowManager/Shell/src/com/android/wm/shell',
    'os/overlays/frameworks/base/packages/SystemUI/src/com/android/systemui/roseshade',
    'os/overlays/packages/apps/RoseKeyboard/src/dev/pixel/rose/keyboard',
    'os/patches/rose-edge-wmshell.patch',
    'os/patches/rose-switcher-launcher3.patch',
]
HEX = re.compile(r'0x([0-9a-fA-F]{8})\b')
DERIVE_KEYS = ['hue', 'neutralHue', 'chromaCap', 'chromaScale', 'neutralScale', 'darkLift', 'lightPull', 'accentL']
ARGB = re.compile(r'^#[0-9A-F]{8}$')
ID = re.compile(r'^[a-z][a-z0-9_]*$')


def load():
    return json.loads(DEFINITION.read_text())


def problems(d):
    """Everything wrong with the definition, as messages (empty when it is valid)."""
    out = []
    themes = d.get('themes') or []
    ids = [t.get('id') for t in themes]
    if len(set(ids)) != len(ids):
        out.append('theme ids must be unique')
    if d.get('default') not in ids:
        out.append('default must name a theme')
    words = {}
    for t in themes:
        tid = t.get('id', '?')
        if not ID.match(tid or ''):
            out.append('%s: id must be lower-case letters, digits or _' % tid)
        if not t.get('label'):
            out.append('%s: label missing' % tid)
        for w in t.get('spoken') or []:
            if w in words:
                out.append('%s: spoken word %r also names %s' % (tid, w, words[w]))
            words[w] = tid
        wp = t.get('wallpapers') or {}
        if wp.get('default') not in (wp.get('ids') or []):
            out.append('%s: default wallpaper must be one of its own' % tid)
        if wp.get('kind') == 'raster':
            if not DRAWABLE.match(wp.get('drawable') or '') or not (wp.get('asset') or '').endswith('.png'):
                out.append('%s: a raster wallpaper needs a .png asset and a drawable name rose_[a-z0-9_]+' % tid)
            if wp.get('align', 'top') not in ('top', 'center'):
                out.append('%s: wallpaper align must be top or center' % tid)
            if wp.get('roseScrim') is not None and not ARGB.match(wp['roseScrim']):
                out.append('%s: roseScrim must be #AARRGGBB' % tid)
            asset = THEMES / wp.get('asset', '')
            if not asset.is_file():
                out.append('%s: wallpaper asset %s missing' % (tid, wp.get('asset')))
            elif hashlib.sha256(asset.read_bytes()).hexdigest() != wp.get('sha256'):
                out.append('%s: wallpaper asset hash differs from the definition' % tid)
        elif wp.get('kind') != 'procedural':
            out.append('%s: wallpaper kind must be procedural or raster' % tid)
        der = t.get('derive')
        if der is not None and der.get('mode') == 'table':
            # A table theme (Winds): every Oxblood artwork colour has a reviewed value, drawn only from its palette.
            palette = {v.upper()[-6:] for v in (t.get('palette') or {}).values() if ARGB.match(v)}
            if not palette or len(palette) != len(t.get('palette') or {}):
                out.append('%s: a table theme needs a palette of #AARRGGBB colours' % tid)
            for key in ('destructive', 'warning'):
                if not ARGB.match('#FF' + (der.get(key) or '#')[1:]):
                    out.append('%s: derive lacks %s' % (tid, key))
                elif der[key][1:].upper() not in palette:
                    out.append('%s: derive %s is not a palette colour' % (tid, key))
            for k, v in (der.get('table') or {}).items():
                if not ARGB.match(k) or not ARGB.match(v):
                    out.append('%s: table %r -> %r is not #AARRGGBB' % (tid, k, v))
                elif v[3:] not in palette:
                    out.append('%s: table %s -> %s is not a palette colour' % (tid, k, v))
            if not der.get('table'):
                out.append('%s: a table theme needs its table' % tid)
        elif der is not None:
            missing = [k for k in DERIVE_KEYS + ['destructive'] if k not in der]
            if missing:
                out.append('%s: derive lacks %s' % (tid, missing))
        if not isinstance(t.get('light', False), bool):
            out.append('%s: light must be true or false' % tid)
        for surface, keys in d['surfaceKeys'].items():
            got = (t.get('surfaces') or {}).get(surface) or {}
            if sorted(got) != sorted(keys):
                out.append('%s.%s: keys must be exactly %s' % (tid, surface, keys))
            for k, v in got.items():
                if not ARGB.match(v):
                    out.append('%s.%s.%s: %r is not #AARRGGBB' % (tid, surface, k, v))
    # Wallpaper ids are global: one id always means one theme's wallpaper.
    seen = {}
    for t in themes:
        for w in (t.get('wallpapers') or {}).get('ids') or []:
            if w in seen:
                out.append('wallpaper %r belongs to both %s and %s' % (w, seen[w], t['id']))
            seen[w] = t['id']
    return out


def const(name):
    return re.sub(r'(?<!^)([A-Z])', r'_\1', name).upper()


def jstr(s):
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def argb(v):
    return '0x' + v[1:].upper()


def java(d, package):
    themes = d['themes']
    n = len(themes)
    L = []
    w = L.append
    w('/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */')
    w('// GENERATED by scripts/rose-themes.py from themes/rose-themes.json. Do not edit; edit the JSON and run')
    w('// `python3 scripts/rose-themes.py --write`. The same file is generated into every package that draws Rose UI.')
    w('package %s;' % package)
    w('')
    w('import java.util.Arrays;')
    w('import java.util.Collections;')
    w('import java.util.List;')
    w('')
    w('/**')
    w(' * Rose themes (plan 1E): stable ids, wallpapers, semantic surface colours and the artwork colour derivation.')
    w(' *')
    w(' * <p>Pure Java: no Android types, so every package and the host tests can use it. An unknown or missing id is')
    w(' * {@link #DEFAULT}; nothing here throws for an id a newer build wrote. {@link #map} derives a theme\'s colour')
    w(' * from an Oxblood artwork colour (identity for Oxblood); {@link #warm} keeps warning, destructive and')
    w(' * microphone-muted colours warm in every theme. Surface colours are exact per theme, read with color().')
    w(' */')
    w('public final class RoseTheme {')
    w('    private RoseTheme() {}')
    w('')
    for t in themes:
        w('    public static final String %s = %s;' % (const(t['id']), jstr(t['id'])))
    w('    public static final String DEFAULT = %s;' % const(d['default']))
    w('    /** The Settings.Global key SystemUI mirrors the theme id to, for Launcher3 and the keyboard. */')
    w('    public static final String SETTING_KEY = %s;' % jstr(d['settingKey']))
    w('    /** The tray link STATE bundle key that carries the theme id from Rose Agent to SystemUI. */')
    w('    public static final String TRAY_KEY = %s;' % jstr(d['trayKey']))
    w('    public static final List<String> IDS = Collections.unmodifiableList(Arrays.asList(%s));'
      % ', '.join(const(t['id']) for t in themes))
    w('')
    w('    private static final String[] LABELS = {%s};' % ', '.join(jstr(t['label']) for t in themes))
    w('    private static final String[][] SPOKEN = {%s};' % ', '.join(
        '{' + ', '.join(jstr(s) for s in t.get('spoken') or []) + '}' for t in themes))
    w('    private static final String[][] WALLPAPERS = {%s};' % ', '.join(
        '{' + ', '.join(jstr(s) for s in t['wallpapers']['ids']) + '}' for t in themes))
    w('    private static final String[] DEFAULT_WALLPAPER = {%s};' % ', '.join(
        jstr(t['wallpapers']['default']) for t in themes))
    w('    private static final boolean[] RASTER = {%s};' % ', '.join(
        'true' if t['wallpapers']['kind'] == 'raster' else 'false' for t in themes))
    w('    private static final boolean[] CENTRED = {%s};' % ', '.join(
        'true' if t['wallpapers'].get('align') == 'center' else 'false' for t in themes))
    w('    private static final int[] ROSE_SCRIM = {%s};' % ', '.join(
        argb(t['wallpapers']['roseScrim']) if t['wallpapers'].get('roseScrim') else '0' for t in themes))
    w('    private static final String[] DRAWABLE = {%s};' % ', '.join(
        jstr(t['wallpapers']['drawable']) if t['wallpapers']['kind'] == 'raster' else 'null' for t in themes))
    derive = []
    for t in themes:
        der = t.get('derive')
        if der is None or der.get('mode') == 'table':
            derive.append('null')
        else:
            derive.append('{' + ', '.join(repr(float(der[k])) for k in DERIVE_KEYS) + '}')
    w('    /** hue, neutralHue, chromaCap, chromaScale, neutralScale, darkLift, lightPull, accentL; null = identity. */')
    w('    private static final double[][] DERIVE = {%s};' % ', '.join(derive))
    w('    private static final int[] DESTRUCTIVE = {%s};' % ', '.join(
        argb('#FF' + t['derive']['destructive'][1:]) if t.get('derive') else '0' for t in themes))
    w('    /** A table theme\'s warning colour (attention, needs-you): amber stays amber, never its destructive red. */')
    w('    private static final int[] WARNING = {%s};' % ', '.join(
        argb('#FF' + t['derive']['warning'][1:]) if (t.get('derive') or {}).get('mode') == 'table' else '0' for t in themes))
    w('    /** Light themes draw dark ink on light surfaces (status and navigation bar icons go dark). */')
    w('    private static final boolean[] LIGHT = {%s};' % ', '.join(
        'true' if t.get('light') else 'false' for t in themes))
    # Table themes: sorted Oxblood literals and their reviewed values (binary search), null for other themes.
    froms, tos = [], []
    for t in themes:
        der = t.get('derive') or {}
        if der.get('mode') == 'table':
            # Sorted as Java sees an int (signed), since map() binary-searches it: 0xFF... sorts before 0x0E....
            signed = lambda x: x - (1 << 32) if x >= 1 << 31 else x
            pairs = sorted(((int(k[1:], 16), int(v[1:], 16)) for k, v in der['table'].items()), key=lambda p: signed(p[0]))
            froms.append('{' + ', '.join('0x%08X' % a for a, _ in pairs) + '}')
            tos.append('{' + ', '.join('0x%08X' % b for _, b in pairs) + '}')
        else:
            froms.append('null')
            tos.append('null')
    w('    private static final int[][] TABLE_FROM = {%s};' % ', '.join(froms))
    w('    private static final int[][] TABLE_TO = {%s};' % ', '.join(tos))
    w('')
    w('    /** The index of a known theme id, or of DEFAULT. */')
    w('    static int index(String id) {')
    w('        int i = id == null ? -1 : IDS.indexOf(id);')
    w('        return i >= 0 ? i : IDS.indexOf(DEFAULT);')
    w('    }')
    w('')
    w('    public static boolean known(String id) { return id != null && IDS.contains(id); }')
    w('')
    w('    /** The id itself when known, otherwise DEFAULT. */')
    w('    public static String normalize(String id) { return IDS.get(index(id)); }')
    w('')
    w('    public static String label(String id) { return LABELS[index(id)]; }')
    w('')
    w('    /** The theme a spoken or typed name means, or null: "glacier", "Switch to Glacier" or "the original". */')
    w('    public static String fromSpoken(String words) {')
    w('        if (words == null) return null;')
    w('        String s = words.toLowerCase(java.util.Locale.ROOT).trim();')
    w('        for (int i = 0; i < IDS.size(); i++) if (s.equals(IDS.get(i))) return IDS.get(i);')
    w('        for (int i = 0; i < SPOKEN.length; i++) for (String w : SPOKEN[i]) if (s.equals(w)) return IDS.get(i);')
    w('        return null;')
    w('    }')
    w('')
    w('    /** The wallpapers that belong to this theme; no other theme may draw them. */')
    w('    public static List<String> wallpapers(String id) {')
    w('        return Collections.unmodifiableList(Arrays.asList(WALLPAPERS[index(id)]));')
    w('    }')
    w('')
    w('    public static String defaultWallpaper(String id) { return DEFAULT_WALLPAPER[index(id)]; }')
    w('')
    w('    /** The theme a wallpaper id belongs to, or null. */')
    w('    public static String wallpaperTheme(String wallpaper) {')
    w('        for (int i = 0; i < WALLPAPERS.length; i++) for (String w : WALLPAPERS[i]) if (w.equals(wallpaper)) return IDS.get(i);')
    w('        return null;')
    w('    }')
    w('')
    w('    /** Whether this theme\'s wallpaper is a raster image rather than procedural artwork. */')
    w('    public static boolean rasterWallpaper(String id) { return RASTER[index(id)]; }')
    w('')
    w('    /** Whether this theme\'s raster wallpaper is centred vertically (otherwise top-aligned); both cover, centred across. */')
    w('    public static boolean wallpaperCentred(String id) { return CENTRED[index(id)]; }')
    w('')
    w('    /** The uniform scrim Rose\'s field draws over this theme\'s raster wallpaper (its quieter field), or 0 for none. */')
    w('    public static int roseScrim(String id) { return ROSE_SCRIM[index(id)]; }')
    w('')
    w('    /** Whether this theme is light: dark ink on light surfaces, so system bar icons must be dark. */')
    w('    public static boolean light(String id) { return LIGHT[index(id)]; }')
    w('')
    w('    /** The drawable (no extension) every package carries this theme\'s raster wallpaper as, or null. */')
    w('    public static String wallpaperDrawable(String id) { return DRAWABLE[index(id)]; }')
    w('')
    w('    /**')
    w('     * This theme\'s colour for an Oxblood artwork colour (fields, plates, pearls, glyphs). Identity for a theme')
    w('     * without a derivation. The alpha channel is kept. Exactly the approved prototype\'s map, checked against it.')
    w('     */')
    w('    public static int map(String theme, int argb) {')
    w('        int t = index(theme);')
    w('        if (TABLE_FROM[t] != null) {')
    w('            // A table theme: the reviewed value for this exact Oxblood colour (alpha included). A colour not in')
    w('            // the table is drawn unchanged rather than guessed; test_rose_themes requires every native one.')
    w('            int at = java.util.Arrays.binarySearch(TABLE_FROM[t], argb);')
    w('            return at >= 0 ? TABLE_TO[t][at] : argb;')
    w('        }')
    w('        double[] p = DERIVE[t];')
    w('        if (p == null) return argb;')
    w('        double[] hls = hls(argb);')
    w('        double h = hls[0], l = hls[1], s = hls[2], hd = h * 360;')
    w('        boolean warm = hd >= 300 || hd <= 55;')
    w('        if (s < 0.02 || !warm) return argb;')
    w('        double s2, h2;')
    w('        if (s > 0.35 && l < 0.92) {')
    w('            s2 = Math.min(s * p[3], p[2]);')
    w('            h2 = p[0];')
    w('        } else {')
    w('            s2 = s * p[4];')
    w('            h2 = p[1];')
    w('        }')
    w('        if (l < 0.12) l = Math.min(0.12, l + p[5]);')
    w('        else if (s2 > 0.3 && l > 0.45) l = l + (p[7] - l) * p[6];')
    w('        return rgb(argb >>> 24, h2 / 360, l, s2);')
    w('    }')
    w('')
    w('    /**')
    w('     * A warning, destructive or microphone-muted colour in this theme: it stays warm. A light warm foreground')
    w('     * takes the theme\'s destructive colour; anything else is unchanged. Identity without a derivation.')
    w('     */')
    w('    public static int warm(String theme, int argb) {')
    w('        int i = index(theme);')
    w('        if (TABLE_FROM[i] != null) {')
    w('            // A light table theme: every warm, saturated colour (muted ink, ring, glow, fill, stop) keeps its')
    w('            // meaning with its own alpha, so it reads on light surfaces and is never its accent: reds take the')
    w('            // destructive colour, ambers (attention, needs-you) the warning colour.')
    w('            double[] w = hls(argb);')
    w('            double wd = w[0] * 360;')
    w('            if (w[2] > 0.2 && (wd >= 300 || wd <= 20)) return (argb & 0xFF000000) | (DESTRUCTIVE[i] & 0xFFFFFF);')
    w('            if (w[2] > 0.2 && wd > 20 && wd <= 60) return (argb & 0xFF000000) | (WARNING[i] & 0xFFFFFF);')
    w('            return argb;')
    w('        }')
    w('        if (DERIVE[i] == null) return argb;')
    w('        double[] hls = hls(argb);')
    w('        double hd = hls[0] * 360;')
    w('        if (hls[2] > 0.35 && hls[1] >= 0.6 && (hd >= 300 || hd <= 30)) return (argb & 0xFF000000) | (DESTRUCTIVE[i] & 0xFFFFFF);')
    w('        return argb;')
    w('    }')
    w('')
    w('    /** Every element of colours through map(). */')
    w('    public static int[] map(String theme, int[] colours) {')
    w('        int[] out = new int[colours.length];')
    w('        for (int i = 0; i < colours.length; i++) out[i] = map(theme, colours[i]);')
    w('        return out;')
    w('    }')
    w('')
    w('    // Python colorsys, line for line (the prototype\'s map is written with it), so results agree exactly.')
    w('    static double[] hls(int argb) {')
    w('        double r = ((argb >> 16) & 255) / 255.0, g = ((argb >> 8) & 255) / 255.0, b = (argb & 255) / 255.0;')
    w('        double maxc = Math.max(r, Math.max(g, b)), minc = Math.min(r, Math.min(g, b));')
    w('        double sumc = maxc + minc, rangec = maxc - minc, l = sumc / 2.0;')
    w('        if (minc == maxc) return new double[]{0.0, l, 0.0};')
    w('        double s = l <= 0.5 ? rangec / sumc : rangec / (2.0 - maxc - minc);')
    w('        double rc = (maxc - r) / rangec, gc = (maxc - g) / rangec, bc = (maxc - b) / rangec, h;')
    w('        if (r == maxc) h = bc - gc;')
    w('        else if (g == maxc) h = 2.0 + rc - bc;')
    w('        else h = 4.0 + gc - rc;')
    w('        return new double[]{mod1(h / 6.0), l, s};')
    w('    }')
    w('')
    w('    static int rgb(int alpha, double h, double l, double s) {')
    w('        double r, g, b;')
    w('        if (s == 0.0) {')
    w('            r = g = b = l;')
    w('        } else {')
    w('            double m2 = l <= 0.5 ? l * (1.0 + s) : l + s - (l * s), m1 = 2.0 * l - m2;')
    w('            r = v(m1, m2, h + 1.0 / 3.0);')
    w('            g = v(m1, m2, h);')
    w('            b = v(m1, m2, h - 1.0 / 3.0);')
    w('        }')
    w('        return (alpha << 24) | (channel(r) << 16) | (channel(g) << 8) | channel(b);')
    w('    }')
    w('')
    w('    private static double v(double m1, double m2, double hue) {')
    w('        hue = mod1(hue);')
    w('        if (hue < 1.0 / 6.0) return m1 + (m2 - m1) * hue * 6.0;')
    w('        if (hue < 0.5) return m2;')
    w('        if (hue < 2.0 / 3.0) return m1 + (m2 - m1) * (2.0 / 3.0 - hue) * 6.0;')
    w('        return m1;')
    w('    }')
    w('')
    w('    /** Python\'s float x % 1.0 (the result takes the sign of the divisor). */')
    w('    private static double mod1(double x) {')
    w('        double m = x % 1.0;')
    w('        return m != 0 && m < 0 ? m + 1.0 : m;')
    w('    }')
    w('')
    w('    /** Python\'s round(x * 255): ties to even. */')
    w('    private static int channel(double x) { return Math.max(0, Math.min(255, (int) Math.rint(x * 255))); }')
    for surface, keys in d['surfaceKeys'].items():
        cls = surface[0].upper() + surface[1:]
        w('')
        w('    /** %s colours per theme, exact (#AARRGGBB in themes/rose-themes.json). */' % cls)
        w('    public static final class %s {' % cls)
        w('        private %s() {}' % cls)
        for i, k in enumerate(keys):
            w('        public static final int %s = %d;' % (const(k), i))
        rows = []
        for t in themes:
            vals = [argb(t['surfaces'][surface][k]) for k in keys]
            rows.append('{' + ', '.join(vals) + '}')
        w('        private static final int[][] COLORS = {')
        for i, r in enumerate(rows):
            w('            %s%s // %s' % (r, ',' if i < n - 1 else '', themes[i]['id']))
        w('        };')
        w('')
        w('        public static int color(String theme, int key) { return COLORS[index(theme)][key]; }')
        w('    }')
    w('}')
    return '\n'.join(L) + '\n'


def reference():
    data = REFERENCE.read_bytes()
    if hashlib.sha256(data).hexdigest() != REFERENCE_SHA256:
        raise SystemExit('themes/reference/glacier.py differs from the approved prototype copy')
    spec = importlib.util.spec_from_file_location('glacier_reference', REFERENCE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def golden_inputs():
    seen = set()
    for rel in GOLDEN_SOURCES:
        p = ROOT / rel
        files = sorted(p.rglob('*.java')) if p.is_dir() else [p]
        for f in files:
            if f.name == 'RoseTheme.java':
                continue
            for m in HEX.finditer(f.read_text(errors='replace')):
                seen.add(int(m.group(1), 16))
    # A coarse lattice too, so the port is checked beyond today's literals.
    for r in range(0, 256, 17):
        for g in range(0, 256, 17):
            for b in range(0, 256, 17):
                seen.add(0xFF000000 | r << 16 | g << 8 | b)
    return sorted(seen)


def golden_text(d):
    ref = reference()
    glacier = next(t for t in d['themes'] if t['id'] == 'glacier')
    der = glacier['derive']
    expect = {'hue': der['hue'], 'neutral_hue': der['neutralHue'], 'chroma_cap': der['chromaCap'],
              'chroma_scale': der['chromaScale'], 'neutral_scale': der['neutralScale'], 'dark_lift': der['darkLift'],
              'light_pull': der['lightPull'], 'accent_l': der['accentL']}
    for k, v in expect.items():
        if ref.PALETTE[k] != v:
            raise SystemExit('derive.%s (%r) differs from the prototype PALETTE (%r)' % (k, v, ref.PALETTE[k]))
    dest = '#%02X%02X%02X' % ref.PALETTE['destructive']
    if dest != der['destructive']:
        raise SystemExit('derive.destructive differs from the prototype')
    lines = ['# Glacier golden values from themes/reference/glacier.py: input, _map_rgb (map), warm_colors (warm)']
    for c in golden_inputs():
        r, g, b = (c >> 16) & 255, (c >> 8) & 255, c & 255
        mr, mg, mb = ref._map_rgb(r, g, b)
        warm = ref.warm_colors('#%02x%02x%02x' % (r, g, b))
        wr, wg, wb = int(warm[1:3], 16), int(warm[3:5], 16), int(warm[5:7], 16)
        a = c & 0xFF000000
        lines.append('%08X %08X %08X' % (c, a | mr << 16 | mg << 8 | mb, a | wr << 16 | wg << 8 | wb))
    return '\n'.join(lines) + '\n'


TABLE_GOLDEN = THEMES / 'reference' / 'table-themes-golden.txt'


def table_golden_text(d):
    """Every table theme's entries, so the Java lookup is checked against the definition entry by entry."""
    lines = ['# theme, Oxblood colour, its reviewed value (themes/rose-themes.json derive.table)']
    for t in d['themes']:
        der = t.get('derive') or {}
        if der.get('mode') == 'table':
            for k in sorted(der['table']):
                lines.append('%s %s %s' % (t['id'], k[1:], der['table'][k][1:]))
    return '\n'.join(lines) + '\n'


def outputs(d):
    out = {rel: java(d, pkg) for rel, pkg in TARGETS.values()}
    out[str(GOLDEN.relative_to(ROOT))] = golden_text(d)
    out[str(TABLE_GOLDEN.relative_to(ROOT))] = table_golden_text(d)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--write', action='store_true')
    mode.add_argument('--check', action='store_true')
    args = ap.parse_args()
    d = load()
    bad = problems(d)
    if bad:
        print('\n'.join(bad), file=sys.stderr)
        return 1
    stale = []
    for src, copies in asset_copies(d).items():
        data = (THEMES / src).read_bytes()
        for rel in copies:
            p = ROOT / rel
            if args.write and (not p.exists() or p.read_bytes() != data):
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
                print('wrote', rel)
            elif not p.exists() or p.read_bytes() != data:
                stale.append(rel)
    for rel, text in outputs(d).items():
        p = ROOT / rel
        if args.write:
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists() or p.read_text() != text:
                p.write_text(text)
                print('wrote', rel)
        elif not p.exists() or p.read_text() != text:
            stale.append(rel)
    if stale:
        print('stale (run python3 scripts/rose-themes.py --write):\n  ' + '\n  '.join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
