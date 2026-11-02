# Rose HiFi prototype

A separate, playable high-fidelity web prototype of RoseOS. It is built from the
HiFi Prototype Plan dated 2026-10-29 and its four reference images. The two
baselines were inputs only:

| Baseline | Role | Revision |
| --- | --- | --- |
| `prototypes/rose-system-interactions` (removed from the tree after HiFi; recover with `git show 9664372:prototypes/rose-system-interactions/Rose-System-Interactions.html`) | Behavior baseline (Rose-Fold-v15 snapshot) | `9664372b6609545b1961626a7d9a0cede04593f4`; HTML SHA-256 `5e265dba824252ad948dbb805cf415daf08f23001bbcf36cb8823106bfed0128` |
| `prototypes/rose-three-dimensions` | WebGL animation and signal mapping | `1a834e8776b0709973ae7fa2a4bc3232e1ec97c9`; HTML SHA-256 `48befc4d6028bce24e5500435a2d7827700c609829121bd8803b93ed4a5a76db` |

This is an isolated browser experiment, not production app or OS code. Voice,
work and app content are local simulations. It does not use a real microphone,
an Android virtual display, glasses or a backend.

## Run

Open `Rose-HiFi.html` in a WebGL-capable browser. The file is self-contained and
needs no network access, installation or permissions. Simulation controls sit
outside the phone: layout, lock, reset, voice events, task events, reduced
motion, the static fallback and WebGL context loss.

The prototype covers these surfaces:

- **Home**: three pages, including a full page of apps. A persistent brushed-metal
  tray holds Rose and the overview symbol (three separate rounded vertical
  rectangles). Apps open simulated app views.
- **Rose**: opening Rose connects a simulated call. Rose answers with formatted
  results. The call controls are mic, end call and Rose's apps. There is no
  composer, Interrupt control or listening/speaking toast.
- **System switcher**: a large human-app carousel and a smaller agent-window
  carousel, which move independently. The connector animates only while a task
  is running.
- **Rose's apps**: eight agent windows across three tasks. Unfolded shows a 4×2
  grid; folded shows a two-column scrolling list. Each window has a stop control
  for its own task, and you can open any window to inspect it.
- **Stop**: confirmation, then Stopping, then Stopped after a simulated
  acknowledgement. Every window of that task updates. Unrelated tasks continue,
  and there is no Stop All.

## Source and build

`src/` holds the editable source:

| File | Contents |
| --- | --- |
| `model.js` | Pure state model |
| `renderer.js` | Single shared WebGL Rose |
| `app.js` | Interface, transitions and gestures |
| `views.js` | Switcher, agents, detail views and the Stop sheet |
| `icons.js` | Icons |
| `style.css` | Material system |
| `index.html` | Page markup |

`build.py` inlines the source and assets into `Rose-HiFi.html`:

```sh
python3 prototypes/rose-hifi/build.py
```

## Rose renderer

One WebGL context serves every Rose placement. The canvas moves between the
Home tray, the Rose stage and the switcher connector, so no thumbnail gets its
own live context.

The shader is the three-dimensions study's procedural hemisphere renderer,
adapted in three ways: a transparent background, 4× supersampling, and a fixed
lens so Rose fits tray wells and headers. The signal mapping is unchanged:

| Signal | Mapping |
| --- | --- |
| Mic on / muted | Rose metal / silver metal |
| Connected / connecting / disconnected | Upper-shell gap of 0.012 / 0.10 / 0.38 |
| Quiet / listening / speaking | No surround / close surround / broad surround |

Rose may speak while the mic is muted.

Lighting differs from the study on purpose. The study's environment has two
equal point highlights, which read as a pair of eyes on a small orb. Here a
single key light at the upper left (with a crisp glint) and a dim, broad fill
replace them, matching the light direction of the tray and icons. The signal
mapping is unchanged.

The rose metal reflects the environment's brightness rather than its blue
cast, with extra saturation, so it reads as a vivid rose. Silver (muted) keeps
the cool reflection.

Safeguards:

- The backing store is capped at DPR 2 and 360 px per side.
- The animation loop runs only while a surround is visible or a transition is
  moving.
- A hidden page cancels animation frames.
- Reduced motion renders single static frames.
- Context loss switches to a CSS fallback that shows the same three signals,
  and restoring the context rebuilds the renderer.

## Validation

```sh
node prototypes/rose-hifi/verify.cjs
```

```sh
node prototypes/rose-hifi/browser-check.cjs prototypes/rose-hifi/out
```

- `verify.cjs` runs model checks in Node. It covers independence of the state
  axes, the cue whitelist, the Stop lifecycle, layout preservation, lock and
  navigation. It does not test rendering.
- `browser-check.cjs` is a Playwright walkthrough in real Chromium. It uses real
  pointer and keyboard input, captures screenshots, and records console output,
  renderer status and requestAnimationFrame intervals.

Evidence recorded in authoring sessions belongs to that build and environment
only. It is not physical-device acceptance:

- Headless Chromium 153.0.8010.12 rendered WebGL through ANGLE with SwiftShader,
  which is software Vulkan.
- The rAF intervals measured there are not GPU timings or device performance.
- Chromium logs "GPU stall due to ReadPixels" performance warnings, capped at
  four per browser process. An independent reviewer reproduced the warning on a
  minimal page that only clears a visible WebGL canvas once per frame. The
  canvas options made no difference: alpha, premultipliedAlpha,
  preserveDrawingBuffer, CSS transforms and re-parenting all produced it. Only a
  hidden canvas suppressed it. It is headless software-compositing readback.
  The app itself makes no readPixels calls.
- Physical-device rendering, touch feel and GPU performance remain unverified.

## Visual direction: Oxblood (selected for this prototype)

Nathan selected the Oxblood direction for this high-fidelity prototype on 2026-11-02, for the prototype only. Production reference `ce126f56` is unchanged. Oxblood is now the default (`Rose-HiFi.html`). The previous look and its ten tray options stay available through `?theme=classic` or `?tray=N`, unchanged from `c76d0a2`, and the controls panel has a **Visual direction** selector.

The direction comes from Notion's "Rose — Sunlit Instruments | Visual Study". The reference images are `Rose-01-Home-and-Agent.png` (SHA-256 `9743db7a…`), `Rose-02-Work-and-Switcher.png` (`10216bf9…`) and `Rose-03-Oxblood-Conversation.png` (`1ac16dff…`), viewed through Notion's file tools. They are generated concept boards, not app screenshots. This prototype does not embed them. The fields in `src/assets/oxblood-*.svg` are original procedural SVG.

What changes in `src/oxblood.css` and the markup:

- **Material:** deep oxblood and graphite with platinum and warm sunlit edges, oblique glass planes, no grain and no blue.
- **Trays:** small skewed instrument trays.
  - Home keeps three controls: Rose, Rose's voice (output) mute and Overview.
  - Rose's tray holds only the microphone and end call.
  - Rose's apps keeps back, end and the manager.
- **Navigation:** Background Work opens from a separate upper-right glass button. It is highlighted only while a task is running.
- **Previews:** app previews in Rose's apps and the switcher are desaturated and tinted to sit in the field, and the detail views show them clear. This is an aesthetic treatment, not a privacy guarantee.
- **Switcher:** it keeps the large human carousel, the smaller agent carousel and no persistent tray.

Behaviour is unchanged:

- Mic mute, Rose's voice mute, ending the call and Stop stay independent.
- Ending a call or muting never stops background work.
- Accessible names and the 44 px minimum targets are kept.

To run the walkthrough in each mode:

```sh
node prototypes/rose-hifi/browser-check.cjs
```

```sh
ROSE_THEME=classic node prototypes/rose-hifi/browser-check.cjs
```

```sh
ROSE_TRAY=N node prototypes/rose-hifi/browser-check.cjs
```

These run Oxblood (the default), Classic, and Classic with tray option N.

## Bottom-tray options (exploration)

`src/tray-variants.css` holds ten opt-in bottom-tray directions, requested by
Nathan on 2026-10-31. They are prompts, not a selection, and the default tray is
unchanged. To try one, open `Rose-HiFi.html?tray=N` with N from 1 to 10, or use
**Tray option** in the controls panel:

| N | Option |
| --- | --- |
| 1 | Slim full-width rail |
| 2 | Flush inset strip |
| 3 | Floating glass lenses |
| 4 | Quiet thin capsule |
| 5 | Bottom edge shelf |
| 6 | Split wings |
| 7 | Continuous glass |
| 8 | Porcelain band |
| 9 | Satin fine metal |
| 10 | Slender open frame |

Every option keeps the same controls, states and minimum 44 px targets in Home,
Rose and Rose's apps. Rendered with motion frozen, the default is pixel-identical
to `4e843d4`.

To capture the options and build a contact sheet:

```sh
node prototypes/rose-hifi/capture-trays.cjs prototypes/rose-hifi/out/trays
```

```sh
python3 prototypes/rose-hifi/contact-sheet.py prototypes/rose-hifi/out/trays
```

To run the full walkthrough with one option applied, set `ROSE_TRAY=N` for
`browser-check.cjs`.

## Review history

Two independent multi-agent reviews ran against this prototype, each in a real
browser:

1. Lenses: requirements, visual fidelity, accessibility and WebGL. Each finding
   went to a separate verifier that tried to refute it.
2. A fix-verification pass checked each finding again and looked for
   regressions.

Every confirmed defect from both passes is fixed. `browser-check.cjs` includes
regression checks for the stuck-UI, lock, focus and target-size findings.

A decorative glass sheen covers the display. It never intercepts input, it is
fainter over reading surfaces, and it follows the pointer slightly. Reduced
motion turns that movement off.

## Provenance

| Asset | Source |
| --- | --- |
| Wallpaper (`src/assets/wallpaper.jpg`) | Extracted unchanged from the behavior baseline at `9664372`. Its README describes it as an original generated interpretation of Nathan's October 29 concept. |
| Firefox, Thunderbird and VLC icons | Extracted unchanged from the behavior baseline (official sources via the Illustrator archive). |
| All other app glyphs, procedural imagery and window content | Original drawings for this prototype. |

App labels and simulated content are fixtures. The reference images guided
composition and materials. Their status-bar icons and incidental labels were
not reproduced.
