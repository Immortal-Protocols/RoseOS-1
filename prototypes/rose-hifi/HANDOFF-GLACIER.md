# GLACIER-1E-1110 — Glacier HiFi variant: handoff

Prototype only. No native Glacier, no production theme selector, no publication. Oxblood is unchanged and
remains the default of `Rose-HiFi.html`; `Rose-Glacier.html` is the same source opened in Glacier. Both
files carry the review harness (Visual direction: Oxblood · Glacier · Classic).

## Files

| Path | Role |
| --- | --- |
| `Rose-Glacier.html` / `Rose-HiFi.html` | Built, self-contained, offline. Same source; Glacier / Oxblood default. |
| `glacier.py` | Build-time Glacier layer: replays every Oxblood rule, in order, under `[data-palette=glacier]` with only colours remapped. Warning/destructive/mic-muted rules keep warm meaning (light warm foregrounds → `#FFABAC`). `oxblood.css` is not edited. |
| `src/glacier.css` | Hand-written Glacier exceptions: field image and placement, switcher glyph. |
| `src/system.js`, `src/system.css` | Shared Oxblood/Glacier system surfaces: F10 keyboard in Messages and notification replies, F09 notification shade, V3 return. Semantic tokens per palette. |
| `src/assets/glacier/` | Illustrator delivery v1: `field.png` = `glacier-portrait.png` (unchanged bytes), tokens, manifest, Illustrator handoff and contrast notes. |
| `glacier-check.cjs` | Review suite: real input at design size, 390 px, 320 px and a 600 px-high screen; composite contrast; capture set and `glacier-check.json` binding. |

Keyboard text model: `prototypes/rose-keyboard/src/keyboard.js` (F10 final source `818a032`), read at build
time and never edited.

## Provenance

- **Illustrator.** GLACIER-1E-1110-ILL-DELIVERY-v1, retrieved through the Notion file route and verified
  against the inbox hashes.
  - ZIP `bf7426fe8d75648257c6c128094e60ab96dd260ae08cec6a1c8de15b80ebbe99`.
  - Portrait 852×1846 `98c7b166df806d0205396c5c2dd80fb470d96dbe2b619662cbd64a8d1099ba3e`, used as-is.
  - Wide 1672×941 `d2dbd5cd…`, not used: phone portrait only.
  - Context study `915dd6d6…`, a reference only; not copied.
- **Palette.** `glacier.tokens.json` v1. It drives the system tokens and the anchors of the derived Oxblood
  colour map.
- **F09 v1.** Notion upload, SHA-256 `5ebc5f0c7c24e737433f4e8d335825ac806a7c952d3d92bf9697d2b5ec42daf1`.
- **V3 return.**
  - Source transfer `Rose-Return-V3-source.zip`, SHA-256 `96e9a6f13e4b3d3bbbe169b5b946a633255e7b57304abb8da7ea35a662d8eced`.
  - `Rose-F11-Return.html`: 42,558 bytes, SHA-256 `798c9a30d04ecc1acde263caaac3552ea4cf2ab1c606a68ed7994348b1955ae5`
    (matches Library v3).
  - Read as source text only; it was not rendered.

## Surfaces and contracts

- **Home and Rose.**
  - Illustrator field set to cover, top-aligned. Original icons and geometry are unchanged.
  - The pearl, connection and work semantics are kept.
- **Chin.**
  - The keyboard rests on a 24 pt rail. Raising the chin moves it 34 pt higher, so it sits 58 pt from the
    bottom.
  - The mic is the only microphone control. Muted is warm `#FFABAC`, with a slash and a label.
- **Notifications (F09).**
  - Pull down from the top edge. Quick settings and brightness.
  - Grouped and expanded Messages; Review opens the exact Rose task and never approves it. Calendar.
  - Inline reply with explicit Send. Sending… blocks repeats. Sent only after acknowledgement.
    "Couldn't send · Retry" keeps the draft.
  - Hiding the keyboard never sends or dismisses.
  - Undo dismiss; Keep draft / Discard. Empty state.
  - Input status mirrors the universal mic; there is no duplicate mute.
- **Keyboard (F10).**
  - As in the study: suggestions, shift and caps, symbols, the edit strip, every hide path, and hardware
    keys scoped to the editor.
  - The conversation follows the bottom only while the person is at the bottom; a resize is not a scroll.
  - No number row and no keyboard mic.
- **Return (V3, reconciled to the exact source).**
  - Footer control at the leading edge of Rose content, above the chin and Disconnect. Chevron plus source
    name, 12 px, a 36 px surface inside a 48 px hit area; no invented icon.
  - A tap requests directly. Pending reads "Returning…", ignores repeats, and shows a separate Cancel that
    invalidates the request ID; late or mismatched acknowledgements are ignored.
  - Departure happens only on an acknowledged success to the exact live task (identity and generation).
    Rose fades and moves 8 px over 200 ms; reduced motion replaces instantly.
  - Success restores the draft, selection, scroll and keyboard. A hidden keyboard stays hidden.
  - A closed or replaced task, or a failed transfer, stays in Rose and shows the exact sheet: "Your source
    app is unavailable." or "Could not return.", "You are still in Rose. Choose where to go; Rose keeps
    working.", with Stay in Rose · Open switcher · Go Home. Nothing is relaunched and the keyboard never
    appears.
  - Rose work continues throughout.
  - The 200 ms departure is re-validated when it runs: if the origin was closed or replaced meanwhile,
    Rose stays with the unavailable sheet and no keyboard; if the person went Home, it is abandoned.
  - **Harness only:** the platform acknowledgement is simulated and selectable in the review harness:
    success or failure after 420 ms (the V3 fixture value) or manual. This is not a UX timing rule. The
    origin task can be set to alive, closed or replaced there too.

## Evidence (bound run: `evidence/glacier-330277ed/glacier-check.json`)

- **Rendered (genuine) evidence.**
  - Headless Chromium + SwiftShader screenshots at 384×832 design, iPhone 13 (390 wide, 654 pt screen),
    iPhone SE (320 wide) and a 390×600 short screen.
  - Real clicks, and CDP touch on phones.
  - Contrast: the foreground is the computed text colour with transitions frozen; the background is the
    rendered pixels beneath the hidden text; the reported figure is the 5th percentile.
  - Results (bound run on `330277ed`, 234/234 checks): every measured text pair is at least 5.90:1 (warm
    error text) and every glyph at least 10.09:1. An earlier run on `da83a139` measured the Home tray mic
    glyph at 9.72:1; the bound run supersedes it.
  - Bound evidence is committed in `evidence/glacier-330277ed/`: `glacier-check.json`, the
    reading-position and return evidence, the review sheet and a SHA-256 manifest.
- **Source-only checks.**
  - The V3 HTML was read and its hash verified; the integration matches its documented contract.
  - Model checks: `verify.cjs` (27) and the keyboard study's model checks.
- **Not covered.** A physical phone, Safari, a native IME, GPU performance, motion review by a person,
  screen-reader runs and native acceptance.

## Open for Design Lead / Home/App

- The V3 footer sits above the HiFi's centred Disconnect. Confirm, or move Disconnect, in the native layout.
- The derived Glacier copy of Oxblood surfaces (work manager cards, switcher, chin aluminium) follows the
  palette mapping rather than hand-picked values per surface. Pixel review is welcome.
