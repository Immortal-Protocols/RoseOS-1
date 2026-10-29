# Rose — Three dimensions

Self-contained WebGL motion study based on Nathan's supplied six-keyframe
reference. Open `Rose-Three-Dimensions.html` in a WebGL-capable browser and select
**Play sequence**, or choose a pose and adjust each signal independently.
No build, dependencies, network access or microphone permission is required.

- **Color / microphone:** rose when on, silver when muted.
- **Split / connection:** joined when connected, narrow gap while connecting,
  wide gap when disconnected.
- **Surround / voice:** none when quiet, close light while listening, broad light
  while speaking. Speaking remains possible while the microphone is muted.

Controls include six presets, independent signal selectors, sequence playback,
pause/reset, simulated speech energy, reduced motion and static fallback.
The renderer uses procedural hemispheres and metallic reflections. The upper
shell moves for connection changes; the light surround responds to simulated
speech. Render resolution is capped at DPR 1.5. Animation stops while hidden;
context loss activates a static CSS fallback.

## Validation

Run the dependency-free behavior check with Node:

```sh
node prototypes/rose-three-dimensions/verify.cjs
```

On 2026-10-29, JavaScript syntax and the behavior checks passed with Node
v24.19.0. The harness executes the actual script using mocked DOM/WebGL calls
and checks presets, independent state axes, resolution cap, pause, reduced
motion, visibility suspension, context-loss/restoration handlers and reset.
It does **not** validate browser layout, pixels, shader compilation/linking,
visual polish or GPU performance.

Browser validation remains pending. The authoring environment's Chromium
154.0.8037.57 sandbox helper had UID 65534 and mode 4755, and no supported browser
control was exposed. No sandbox changes or unsafe launch switches were used.
The HTML reports shader initialization failures and runtime frame intervals in
its study notes when opened in a supported browser. Frame intervals are not GPU
execution timings or physical-device acceptance.

## Provenance

Original prototype created on 2026-10-29 in response to Nathan's direct brief.
The supplied reference is interpreted as procedural geometry; no raster asset,
external texture or extracted 3D model is embedded. This standalone study is not
integrated into the Android app or published as a hosted site.

The HTML bytes match the delivered Library artifact:

- Library ID: `libfile_56fbf96601c4819185264262f9717f99`, version 0.
- SHA-256: `48befc4d6028bce24e5500435a2d7827700c609829121bd8803b93ed4a5a76db`.
- Repository base: `2f58260`.
