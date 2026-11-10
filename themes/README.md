# Rose themes

This is plan 1E. `rose-themes.json` is the only place a Rose theme is defined. Each entry has:

- a stable id, its label and the words people say for it;
- its own wallpapers;
- the exact semantic colours of each system surface (keyboard, shade, return control, field);
- for a theme other than Oxblood, how its artwork colours are derived from Oxblood's.

Rose Agent, SystemUI/WMShell, Launcher3 quickstep and the Rose keyboard are built separately. `scripts/rose-themes.py --write` writes the same pure `RoseTheme.java` into each package that draws Rose UI. It also copies each raster wallpaper byte for byte. `--check` fails when any copy is stale. `scripts/tests/test_rose_themes.py` and `RoseThemeTest` run in `scripts/test-local.sh`.

| Theme | Wallpapers | Artwork colours | Surface colours |
| --- | --- | --- | --- |
| `oxblood` (default) | The curated procedural variants: classic, garnet, ember, velvet, dusk. | Unchanged: map() is the identity. | Exactly what the native renderers draw today (the tests read the sources). |
| `glacier` | `glacier`: the Illustrator's portrait field, cover and top-aligned on `#0B141D`. | The approved prototype's colour map. | The approved Glacier tokens. |

Glacier's artwork colour map comes from `reference/glacier.py`, an unchanged copy of `prototypes/rose-hifi/glacier.py` at prototype revision `2ecceb9779cb491554a561964d753cf327acfa2e` (blob `4654ef46`).

## Rules

- **Wallpapers belong to their theme.** A wallpaper id names exactly one theme. Oxblood's colour transforms are never applied to another theme's wallpaper.
- **Warm meanings stay warm in every theme.** Warning, destructive and microphone-muted colours go through `RoseTheme.warm()`, never `map()`. In Glacier a light warm foreground becomes `#FFABAC`; it never turns blue.
- **Unknown ids fall back.** An unknown or missing id is the default theme, so a value written by a newer build never breaks drawing.
- **A theme changes colours and wallpaper only.** It never changes geometry, app icons, font or Home columns.

## Adding a theme

1. Add an entry to `rose-themes.json`. Give it every surface key, its own wallpaper ids and any raster asset with its SHA-256. Add a `derive` block if its artwork colours derive from Oxblood's.
2. Put its assets under `themes/<id>/`. If it has a raster wallpaper, add the asset to `ASSET_COPIES` in the script.
3. Run `python3 scripts/rose-themes.py --write`, then `bash scripts/test-local.sh`. The contrast tests cover the new theme's opaque pairs.
4. Add the id to the conversational tool's enum (`customization_theme` in `workspace/rose-agent/app/assets/customization-agent-tools.json`). The catalog test checks that it matches.

## Glacier provenance

| File | Source | SHA-256 |
| --- | --- | --- |
| `glacier/field.png` | Illustrator GLACIER-1E-1110-ILL-DELIVERY-v1, `glacier-portrait.png`, unchanged | `98c7b166df806d0205396c5c2dd80fb470d96dbe2b619662cbd64a8d1099ba3e` |
| `glacier/glacier.tokens.json` | The same delivery, v1 | `562f99dda2172252d41b140aad8ec4ae19172bd1be153a66b1a5fd064046f1fc` |
| `glacier/illustrator-contrast.json`, `ILLUSTRATOR-HANDOFF.md`, `illustrator-manifest.json` | The same delivery, as committed with the prototype | (see `illustrator-manifest.json`) |
| `reference/glacier.py` | `prototypes/rose-hifi/glacier.py` at `2ecceb97` | `d26f04697345609649903c4a7dc2aac25523a565d569b0068b4e7289027f8a62` |

- The delivery ZIP's SHA-256 is `bf7426fe8d75648257c6c128094e60ab96dd260ae08cec6a1c8de15b80ebbe99`.
- The approved `Rose-Glacier.html` at `2ecceb97` has SHA-256 `39dd5cebee31311bda7e30f84f59cf5dcf80e928bb399ef559c3c488410ade3b`.
- The keyboard top colour `#0F1B27` is the prototype's own Glacier keyboard value (`system.css`), not an Illustrator token.
- These files come from the approved prototype. They are not native acceptance.
