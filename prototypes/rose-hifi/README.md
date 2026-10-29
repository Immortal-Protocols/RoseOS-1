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
