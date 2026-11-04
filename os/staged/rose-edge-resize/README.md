# Raised edge with real app resize on release: staged E2c (#410), inert

The staged Launcher3 client for Nathan's raised edge with resize on release. It is staged here, under
`os/staged/rose-edge-resize/`, so that nothing applies, copies or builds it:
- `scripts/apply-os-changes.py` reads only `os/patches` and `os/overlays`;
- the compile contract (`os/compile-contracts/launcher3-quickstep-lib.json`) binds #440's E2a candidate at `e361d1c`,
  which stays byte for byte in `os/`.

These files move into `os/` only in a coordinated re-bind, once all four of these hold:
- the framework side (OS Core) exists;
- Home's panel exists;
- the interface is agreed on #410;
- the real Launcher3 compile path is open.

## Behaviour (prototypes/rose-hifi `e15274a`, lines 1278–1286 and 2915–2986)
- A short bottom swipe of more than 36dp raises the 92dp edge.
- On release, the app's real window area loses 58dp of height. Its width is unchanged and nothing is scaled, so the
  app's own bottom bar or composer rides up.
- A long swipe or a fling goes Home, as before.
- Lowering the edge restores the full height.
- The release never reaches the app, because Launcher3 pilfers the pointers when the gesture starts.

## Files
- `aidl/`: **the interface record.** `com/android/wm/shell/roseedge/IRoseEdge{,Listener}.aidl` is WMShell's resize,
  owned by OS Core and agreed on #410. OS Core implements the shell side in #516; Launcher3 implements its half from
  these exact transaction ids. The earlier Home-panel proposal (`IRoseEdgeControls`/`IRoseEdgeHost`) is withdrawn:
  for the mic slot, Home's option (a) uses SystemUI's existing trusted tray client and adds no new trust (#410
  comment 6622900952).
- `overlays/.../fallback/`: the staged Launcher3 client, which keeps E2a's `RoseEdgeGesture` unchanged.
  - `RoseEdgeShell`: hand-written binder code, because Launcher3QuickStepLib compiles no AIDL.
  - `RoseEdgeState`: the edge rises only when an edge window draws it and the shell can really resize the app. A
    refusal or a shell-side lower takes it down.
  - `RoseEdgeSwipe`: asks for the 58dp lift, and does not crop an app the shell has already shortened.
- `native-test/`: compiles the staged sources unchanged against Java that the SDK's own `aidl` tool generates from
  `aidl/`. The fake shell is the generated `IRoseEdge.Stub`, and it replies through the generated
  `IRoseEdgeListener.Proxy`. It runs on Robolectric's SDK 35 runtime:
  ```sh
  cd os/staged/rose-edge-resize/native-test
  ANDROID_SDK_ROOT=/opt/roseos/android-sdk mvn -q -B test
  ```
  It shows wire compatibility both ways, the refusal and lower rules, the default-off switch, the 58dp lift, and that a
  resized app is not cropped again. It does not show WMShell's resize, a compiled Launcher3 or any device behaviour.

- **The edge window** (`RoseEdgeWindow`, `RoseEdgeLayout`) is shown while the edge is up, in a
  `TYPE_NAVIGATION_BAR_PANEL` window. It draws Home's design contract (`qa/oxblood/oxblood-contract.json` `homeEdge`,
  checked key for key in CI): the plate, hairline, Rose and switcher wells, pearl and switcher glyph.
  - The 44dp microphone-input slot is left transparent and outside the window's touchable region. Its screen bounds go
    to Shell through `setMicSlot = 3` (Chief's option (a), agreed with OS Core), only after each `onRaised` and only
    inside the agreed bottom band and 32–64dp size. SystemUI's existing tray control draws and handles the mic there.
    Launcher3 never touches microphone state.
  - Rose and the switcher lower the edge, then call the host's `Actions`. A swipe down lowers it.
  - The harness's `RoseEdgeWindowNativeTest` covers these with native graphics, and writes
    `target/render/rose-edge.png`. It compiles against Robolectric's uninstrumented SDK 35 framework, because the
    window uses the hidden internal-insets touchable region, as Launcher3's Taskbar does.

## Not yet here
- `SystemUiProxy` handing `RoseEdgeShell` the shell's binder.
- `TouchInteractionService` starting `RoseEdgeWindow` with its `Actions`.
- Both are patch hunks for `rose-edge-launcher3.patch`.
- Accessibility nodes for the two wells.
- Both of those join `rose-edge-launcher3.patch` once the interfaces are agreed.
