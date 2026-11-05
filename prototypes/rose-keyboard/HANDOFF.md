# F10 — Rose keyboard: design study and implementation handoff

Prototype only. `Rose-Keyboard.html` is a single self-contained page built from `src/`
(`python3 prototypes/rose-keyboard/build.py`). It is separate from `prototypes/rose-hifi/`
and from production code; nothing here changes the Android IME, the OS gesture work (#548),
the Rose chin implementation or any release. One recommended direction, for the Design Lead
and Nathan to accept or redirect; it is not a production keyboard selection.

Evidence level: headless Chromium (SwiftShader) with real mouse/keyboard input and CDP touch
on emulated iPhone 13, Pixel 7 and iPhone SE. Not a physical phone, not Safari, not a real
Android IME, not performance.

## What the study shows

| Area | Behaviour in the prototype |
| --- | --- |
| Show | Tapping a text field opens the keyboard and places the caret where tapped (index captured at pointer-down, so the moving field never shifts it). Tab focus also opens it. |
| Hide | Swipe down on the suggestion strip · ⌄ at the strip's right end · a tap on **blank** conversation space · Esc · leaving the app (long swipe up, Rose, switcher). Hiding never sends; the draft is always kept. Taps on messages or the header, scrolls, drags and long-presses never hide it. |
| App space | Adjust-resize: the app window's bottom sits on the keyboard's top. The app's own bar (composer, Notes tools) rides above the keyboard. No overlap, no pan. |
| Reading position | The conversation follows the newest message only while it is already at the bottom, or after a send. While reviewing history, opening the keyboard, typing or raising the chin leaves the messages being read exactly in place (the window shortens from below; scroll offset is kept). |
| Typing | QWERTY, key preview pop on press, slide-to-another-key before release, two-thumb rollover (the earlier key commits when the next lands). |
| Shift | Auto-capital at the start, after `. ! ?` and after a new line. Tap = one capital; double-tap (< 350 ms) = caps lock (filled arrow + underline); tap again = off. |
| Symbols | `?123` → numbers/symbols; `=\<` → more symbols; `ABC` → letters. |
| Suggestions | Three slots: typed word (in quotes) · best (bold, middle) · next. A correction is shown in the middle with a ruby underline. Between words: next-word predictions. A lone typed word sits in the middle. |
| Autocorrect | Only unambiguous non-words (list in `keyboard.js`), applied on space, Return and punctuation. Valid words that are often contractions (`ill`, `cant`, `wont`, `well`, `were`, …) are never changed: the contraction is offered as the best suggestion instead. One backspace right after a correction restores exactly what was typed, and that word is not corrected again. Double space → ". ". |
| Editing | Drag the space bar to move the caret (keys dim); hold space then drag to select. Hold ⌫ to repeat, accelerating to whole words after ~1 s; swipe left from ⌫ to delete a word. Long-press text selects the word. Any selection swaps the strip to a tight, grouped **Cut · Copy · Paste │ Select all │ Undo · Redo** (content-width, 32 pt visuals with 44 pt hit areas), which stays up until the next keystroke. Typing replaces a selection. Undo groups a typed word into one step. |
| Action key | Messages: **Send** (IME action, ruby). Notes: **Return** (new line). |
| Hardware keys | Only while the editor itself has focus: typing, ⌫ (Alt+⌫ word), ←/→ (Shift extends), Ctrl/Cmd+Z/Y/A/C/X/V. Enter/Space on a focused control (⌄, a suggestion, an edit tool, the chin's buttons) activate that control and never type or send. Esc lowers the chin, then hides the keyboard. The on-screen keys and strip never take focus from the editor. |

## The keyboard and the Rose chin

The Rose chin is the OS edge that holds Rose, the microphone-input mute and the switcher (F08).
The keyboard never covers or replaces it.

| State | Bottom of the screen, bottom-up | App window ends at |
| --- | --- | --- |
| Keyboard hidden, chin lowered | home handle overlay (24 pt hit area) | screen bottom (app bars pad 28 pt for the handle) |
| Keyboard shown, chin lowered | **rail 24 pt** (keyboard colour, home handle) → keyboard 268 pt | 292 pt from bottom |
| Keyboard shown, chin raised | **chin 92 pt** (concave top) → keyboard sits on the concave top (bottom corners 34 pt) | 268 + 58 = 326 pt |
| Keyboard hidden, chin raised | chin 92 pt | 58 pt (same as the HiFi swipe-up) |

Rules:
1. **Swipe up from the bottom edge always raises the chin, keyboard open or not.** With the keyboard
   open, the edge zone is the 24 pt rail under the space row; the keyboard stays open and rides up
   from the rail (24 pt) to the chin's concave top (58 pt), i.e. **34 pt higher**, with the app above it.
   Rose, the mic and the switcher are unobstructed (hit-tested).
2. **Swipe down on the raised chin lowers it**; the keyboard returns to the rail. Esc lowers the chin first,
   then hides the keyboard.
3. **Long swipe / fling up leaves the app** (Home): keyboard hides, draft kept.
4. **The chin mic is the only microphone control (decided: no keyboard mic).** There is never a second mic with a
   different meaning (consistent with F08's single mute). Muting from the chin never changes the keyboard,
   the field or the draft. While muted, the status bar shows an explicit indicator matching the chin: the
   same slashed-mic glyph and ruby outline, with the words "Mic off" (screen-reader label "Microphone
   muted"), so the state stays visible after the chin lowers.
5. Rose / switcher from the chin leave the app: keyboard hides and the draft is kept. What happens on
   return (restoring focus and the keyboard) belongs to F11 and is not modelled here.
6. The keyboard never starts the chin gesture: swipes that start on keys or the strip belong to the keyboard
   (`touch-action: none` on the keyboard; edge swipes call `preventDefault` on `touchmove` so the
   browser does not turn them into scrolls).

## Geometry (pt, at the 384 pt design width)

| Token | Value |
| --- | --- |
| Suggestion / edit strip | 44 |
| Key height · row gap · key gap | 46 · 8 · 6 |
| Keyboard total | 268 (strip 44 + 4 rows 184 + 3 gaps 24 + padding 16) |
| Rail under keyboard (chin lowered) | 24 |
| Raised chin · its concave inset | 92 · 58 (keyboard sits 34 above its rail position) |
| Letter key minimum | 32.2 × 46 (all keys labelled for screen readers) |
| Hide (⌄) target | 44 × 44 |

Measured app window / conversation height (pt) by state, from the prototype:

| Screen | Hidden | Keyboard | Keyboard + chin | Chin only |
| --- | --- | --- | --- | --- |
| Design 384 × 832 | 832 / 657 | 540 / 383 | 506 / 349 | 774 / 617 |
| Pixel 7 (782 pt tall in-browser) | 782 / 607 | 490 / 333 | 456 / 299 | 724 / 567 |
| iPhone SE (682 pt) | 682 / 507 | 390 / 233 | 356 / 199 | 624 / 467 |
| iPhone 13 Safari with bars (654 pt) | 654 / 479 | 362 / 205 | 328 / 171 | 596 / 439 |

On short screens the keyboard + chin state leaves ~170–200 pt of conversation. That is the
reason the chin raised over the keyboard is a deliberate, temporary state (one swipe down
restores 34 pt) rather than a resting one.

## Implementation notes for the Android IME / OS

- **Insets:** report the keyboard (268 dp + 24 dp rail) through `WindowInsets.Type.ime()`; apps keep
  adjust-resize semantics. When the chin is raised over the IME, the OS should add the chin's 34 dp
  difference to the bottom inset (58 dp total under the IME) rather than overlaying it, so app bars stay
  visible. Animate the IME and chin with the same curve (prototype: 260 ms, `cubic-bezier(.2,.8,.2,1)`)
  via `WindowInsetsAnimation` so app content moves in one piece.
- **Gesture ownership:** the 24 dp rail under the IME belongs to the system gesture (chin / Home), not the IME.
  The IME owns everything from the strip down to the space row.
- **Action key:** take the label/icon from `EditorInfo.imeOptions` (`IME_ACTION_SEND` → ruby Send arrow;
  multi-line → Return).
- **Suggestions/autocorrect:** the prototype lexicon and correction list are placeholders; the
  behaviours to keep are the three-slot order, correction only for unambiguous typos (also on Return and
  punctuation), and one-backspace revert that is not re-corrected.
- **Model:** `src/keyboard.js` is a DOM-free reference for the editing rules (caret/selection, shift
  states, double-space period, word-grouped undo, delete-word, emoji-safe backspace). `model-check.cjs` is
  the executable specification (37 cases).
- **Visual tokens:** keys `#3a2a2e → #2c1f22`, function keys `#1e1417`, keyboard `#1d1114 → #150c0f`,
  action `#c8304a → #97182e`, text `#f2e6d9`, correction underline `#e04a62`. The chin uses the HiFi
  Oxblood edge unchanged.

## Direction (Design Lead review)

- **No number row** on the letters page (keeps 54 pt of app space).
- **No keyboard mic**; voice and the microphone-input mute stay on the Rose chin.
- **Outside-tap dismissal only on blank conversation space**; scrolling, selecting text and notification
  actions must never dismiss the keyboard by accident.
- Out of scope here: emoji and language switching.

## Boundary contracts with neighbouring lanes (not implemented here)

These are the keyboard's obligations at its edges, for the Design Lead to reconcile with the existing
owners. This study does not design or build those lanes.

**F09 — notification tray (Illustrator).** An inline reply from a notification keeps the notification's
context, the draft and the selection visible above the IME + rail/chin insets. Hiding the keyboard there
never sends the reply and never dismisses the notification. Needed from F09: the reply surface's
anchor and minimum visible height above the inset.

**F11 — return gesture (WebGL Prototyper).** On leaving an app (Rose, switcher, Home), the focused editor
and whether the keyboard was visible are preserved. They are restored only after a successful return to the
same live task. A cancelled return stays in Rose with no keyboard. If the origin app or task is no longer
available, the fallback never summons the keyboard. Needed from F11: the success/cancel/unavailable
signals and their timing relative to the IME animation.

## Files

| File | Purpose |
| --- | --- |
| `Rose-Keyboard.html` | Built, self-contained prototype (open in a browser; phone mode on phones, controls behind ⋯). |
| `src/index.html`, `src/style.css`, `src/app.js` | Page, Oxblood styles, interaction layer. |
| `src/keyboard.js` | Pure text/keyboard model. |
| `src/assets/field.svg` | Copy of the HiFi Oxblood Rose field (unchanged artwork). |
| `build.py` | Assembles the HTML. |
| `model-check.cjs` | 37 model checks (`node prototypes/rose-keyboard/model-check.cjs`). |
| `check.cjs` | 83 functional checks with real input + review renders to `out/check/` (ignored by Git). `check.json` records the commit, whether the study tree was clean, and the Git blob of the HTML tested versus the one committed. |

Review-sheet binding: the sheet is captured from the committed HTML after the commit, and labelled with
the commit, the HTML's SHA-256 and its Git blob id. (The first sheet's `89a9fc7957cc…` label was the
SHA-256 of `Rose-Keyboard.html` exactly as committed in `67e1a82`, a content hash of the same build.)
