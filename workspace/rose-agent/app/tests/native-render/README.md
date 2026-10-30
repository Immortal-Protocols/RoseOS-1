# Host-rendered native output of production Rose views (opt-in)

APP-OS-VISUAL-1030-M003. This is not part of the app build, `scripts/test-local.sh`, CI or release, and it is not a
visual gate. It compiles the listed production sources unchanged (`pom.xml` `testIncludes`) and draws them
through Robolectric 4.17's native Android graphics (`@GraphicsMode(NATIVE)`, SDK 35, xxhdpi) into real Bitmaps,
written as PNGs under `target/render/` (ignored; keep evidence out of Git).

```sh
cd workspace/rose-agent/app/tests/native-render
ANDROID_SDK_ROOT=/opt/roseos/android-sdk mvn -q -B test   # the existing compile SDK; android.jar is compile-only
```

Dependencies are pinned and come only from Maven Central and Google's Maven. Robolectric's SDK 35 runtime
(`android-all-instrumented:15-robolectric-13954326-i7`) is fetched by Maven and passed to Robolectric's
documented offline mode; the AAR-only `androidx.test` runtime is unpacked to plain jars.

**Label results "host-rendered native output".** An isolated View/Bitmap draw on a Linux x86_64 host is not a
physical device, full activity or screen composition. Hardware compositing, elevation shadows, timing and touch
are not exercised. Fixtures are production `ConversationState`/`RoseVisualState` values with animation off; nothing
starts VoiceService, pairing or network. Compare only like-for-like regions and states against the selected
`ce126f56c8967008b0a5db889137f224ac9ad36c/prototypes/rose-hifi`, whose Chromium output stays separately labelled.
