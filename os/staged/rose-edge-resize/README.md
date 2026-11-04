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
- `aidl/`: **the interface record.**
  - `com/android/wm/shell/roseedge/IRoseEdge{,Listener}.aidl`: WMShell's resize, owned by OS Core.
  - `dev/pixel/voice/edge/IRoseEdge{Controls,Host}.aidl`: Home's controls on the edge, proposed for Home.

  Each side implements its half from these exact transaction ids.
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

## Not yet here
- `SystemUiProxy` handing `RoseEdgeShell` the shell's binder.
- The edge window that hosts Home's panel.
- Both of those join `rose-edge-launcher3.patch` once the interfaces are agreed.
