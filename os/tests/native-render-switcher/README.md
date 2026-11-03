# Host-rendered native output and framework behaviour of the switcher's Rose carousel (opt-in)

#410 OS Product. This harness compiles the production Launcher3 overlay classes `RoseFan.java` and
`RoseAgentFan.java` unchanged (`pom.xml` `testIncludes`). It runs them through the Android framework's own View,
touch, scroll and animation code, as carried by Robolectric 4.17's SDK 35 runtime. Rendering uses native Android
graphics (`@GraphicsMode(NATIVE)`) at the frozen reference's 384×832 dp screen, xxhdpi. It is the same seam Home uses
in `workspace/rose-agent/app/tests/native-render`, with the same pinned dependencies (Maven Central and Google's
Maven only).

It is not part of any build, `scripts/test-local.sh`, CI or release, and it is not a gate.

```sh
cd os/tests/native-render-switcher
ANDROID_SDK_ROOT=/opt/roseos/android-sdk mvn -q -B test   # android.jar is compile-only
```

## What it shows

`RoseAgentFanNativeTest`:

- **Entry and fan geometry.** On entry the second card is centred when there are three or more. Each card takes the
  frozen reference's computed transform (k = −1, 0, +1, +2 clamped to 1.4), and the centre card is on top.
- **A drag cancelled mid-way still settles on a card.** A real `ACTION_CANCEL` arrives after the fan has taken the
  drag, as when the system gesture takes it. The card is never opened.
- **Flings.**
  - A fling moves exactly one card.
  - A slow drag that starts on a card after a fling still settles. This is the regression the #418 review found.
- **Placement.**
  - Re-entry places afresh.
  - When a card leaves, the centred card keeps its place by identity.
- **Reduced motion.** With animations off (`ValueAnimator` duration scale 0, the system's "Remove animations"),
  release and key steps settle at once, with no frames.
- **Right to left.** The first card is laid out on the right, and placement and keys act by position. The test app
  declares `supportsRtl`, as Launcher3 does.
- **Keys and screen-reader scroll.**
  - Keys and screen-reader scroll move exactly one card.
  - At an end a key is not taken, so focus can leave.
  - A tap on the centre card reaches it.

`RoseEdgeSwipeNativeTest` (#410 E2a) runs the production `RoseEdgeSwipe`, `RoseEdgeState` and `RoseEdgeGesture` on
the same runtime, at xxhdpi and once at 420dpi:
- **Default-off switch.** With `persist.wm.debug.rose_edge` unset, Launcher3 decides everything, nothing is previewed,
  and settling never reaches the state, from any thread. The test reads the runtime's real `SystemProperties` back; the
  compile-only stub under `src/test/java/android/os` exists because `android.jar` omits that hidden class.
- **No edge window, no change.** With no listener registered (E2b's window), nothing rises and nothing is previewed.
- **Gates.** The edge applies only to an unlocked app under gesture navigation in its natural rotation, never when the
  handler excludes the gesture (Home, a trackpad, a continued gesture, a task that is not full screen).
- **Launcher3's own results stand:** anything not Home, a cancel, a pause.
- **Raise and lower, per display and task.** A short swipe raises the edge over its task and tells listeners once.
  Over that task, a short swipe keeps it and more than 120dp goes Home; over another task it is not up. Leaving
  lowers it. Distances count from touch-down (the touch slop is added back) and speed is averaged since touch-down.
- **Preview.** The crop is at full width, unscaled, while the app follows. Past the edge it shrinks about the shortened
  window's centre and lifts 40dp. A raise at release does not snap the window. Attached to recents, or with no size,
  Launcher3's own transform applies. With the edge already up, or reduced motion, the app does not track the finger.
  State is main-thread only.

The patched Launcher3 swipe handlers that call these (`os/patches/rose-edge-launcher3.patch`) are not compiled or
run here.

Touches are real `MotionEvent`s dispatched from the root, so a card consumes `ACTION_DOWN` and the fan intercepts
the drag exactly as Android does. Scrolling is stepped frame by frame through `computeScroll`.

## Labelling

Label PNGs under `target/render/` (ignored; keep evidence out of Git) as **"host-rendered native output"**. Label the
assertions as **framework behaviour on a host**.

What this is not:
- not a device, a full RecentsActivity or screen composition;
- not hardware rendering, elevation shadows, touch feel or timing.

The cards are plain Oxblood plates of the production size. `RoseAgentStrip` itself needs Launcher3 (R, RecentsActivity,
executors, platform APIs) and is not compiled here. Neither are Launcher3's own `RecentsView`/`TaskView`: compiling
those needs the pinned tree on the builder.
