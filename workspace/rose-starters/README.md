# Rose starters: templates × themes

Reusable starting points Rose can build Android apps from. A **template** owns behaviour and state (a feed, a sprite
game, a player). A **theme** owns appearance and the stock controls (Material 3, Minimal, Skeuomorphic). Any theme
applies to any template with one line, with no edits to either and no per-pair adapter:

```kotlin
setContent {
    RoseTheme(MinimalTheme) {      // ← swap the theme here; nothing else changes
        PlayerTemplate.App()
    }
}
```

Starters are a starting point, not a format every Rose app must follow. Plan: "Chief execution plan: Rose app
templates × themes" (Wave 1 framework, Wave 2 the six pilots, Wave 3 validation through Rose's generation path).

## Run the example

```sh
cd workspace/rose-starters
./gradlew :sample:assembleDebug      # sample/build/outputs/apk/debug/sample-debug.apk (debug key, never released)
./gradlew check                      # all unit tests, lint and the boundary rule
```

`:sample`'s `MainActivity` shows the complete hookup. It runs the example template **Tally** (in `:examples`) under
`BaselineTheme`. Change the one marked line to `PaperTheme` and the same app looks different but behaves the same.
- `TallyBehaviorTest` proves the behaviour is the same by running one script under both themes, in light and dark, on API 34 and 37.
- `ThemeSwapTest` swaps the theme at runtime, mid-session, and checks that state and behaviour carry over.
- `SwapScreenshots` renders the four results to `sample/build/screenshots/`.

Gradle needs an Android SDK, via `ANDROID_HOME` or `sdk.dir` in `local.properties`. Accept the licences once; AGP then
downloads platform 37 and build-tools itself.

## Layout and owners

| Path | Package | Owner | What |
|---|---|---|---|
| `design/` | `app.rose.design` | Home/App Product | The contract: `Theme`, `Template`, tokens, stock controls, `RoseTheme`, baseline theme, icons, `ControlKit` |
| `design-testing/` | `app.rose.design.testing` | Home/App Product | `ThemeConformanceTest`, `ThemeCases`, `setThemedContent`, `Contrast` |
| `examples/`, `sample/` | `app.rose.starters.examples`/`.sample` | Home/App Product | Tally + Paper, the runnable example and its tests |
| `templates/feed/` | `app.rose.template.feed` | Feed template engineer | `FeedTemplate` |
| `templates/sprite-game/` | `app.rose.template.spritegame` | Sprite game template engineer | `SpriteGameTemplate` |
| `templates/player/` | `app.rose.template.player` | Player template engineer | `PlayerTemplate` |
| `themes/material3/` | `app.rose.theme.material3` | Material 3 theme engineer | `Material3Theme` |
| `themes/minimal/` | `app.rose.theme.minimal` | Minimal theme engineer | `MinimalTheme` |
| `themes/skeuomorphic/` | `app.rose.theme.skeuomorphic` | Skeuomorphic theme engineer | `SkeuomorphicTheme` |
| `matrix/` | `app.rose.starters.matrix` | Home/App Product; each template engineer owns their `*Scenario.kt` only | All 9 combinations: wiring, conformance, behaviour, a debug app |

Each pilot module is a compiling placeholder (`implemented = false`) that conformance and the matrix skip and report
as skipped. Work only in your own directory, plus your `matrix/src/test/.../<Template>Scenario.kt` for template
engineers. Anything in `design/`, `design-testing/`, `settings.gradle.kts` or `gradle/libs.versions.toml` is an
interface change: ask Home/App Product, so all six stay compatible.

## Rules that make 3 × 3 = 9 with no adapters

The **`checkStarterBoundaries`** task (part of `check`) enforces:
- templates import no theme and no Material;
- themes import no template;
- `design` imports neither.

**Templates**
- Implement `app.rose.design.Template` as an `object` with a stable `id`.
- `App(modifier)` is the whole starter: sample content, screens and state.
  - Keep state in the template, with `rememberSaveable` or a ViewModel.
  - Never branch on `RoseTheme.id`.
- Build every screen from:
  - the stock controls and `Text` in `app.rose.design`;
  - Compose foundation layout (`Row`, `Column`, `Box`, `LazyColumn`, `Canvas`…);
  - `RoseTheme.colors`, `.type`, `.shapes`, `.spacing`.
- Anything you draw yourself, such as a game field, sprites or artwork, takes its colours from the colour roles, so it
  follows the theme and the mode.
- Find controls in tests by what a person sees: text, content descriptions, roles.

**Themes**
- Implement `app.rose.design.Theme` as an `object` with a stable `id`.
- `tokens(dark)` returns colour roles, type, shapes and spacing for light and for dark. Light and dark are modes of one
  theme, not two themes.
- `controls` implements **every** member of `Controls`. They are all abstract in v1, so a theme that compiles is fully
  hooked up.
- Optionally override `Provide` to wrap the app once; the Material 3 theme sets up `MaterialTheme` there.
- Draw every pixel and every press, focus and drag state your way. Keep what `Controls.kt` documents for each control.
  `ThemeConformanceTest` checks all of it, in both modes, on API 34 and 37:
  - apply the caller's `modifier` to the outermost element;
  - call each callback exactly as documented;
  - expose the documented semantics (role, text, state, progress, disabled);
  - show the caller's text as given;
  - touch targets ≥ 48dp;
  - readable contrast between each colour role pair;
  - draw at the size the caller's modifier gives: a full-width button looks full width, a taller Progress or
    Segmented draws taller, and a taller Slider takes taps across its whole height;
  - stay visible on every surface a template may use (background, surface, and an Inset panel's surfaceVariant):
    an empty track, an inactive slider track and a Secondary button's edge must stand out from all three.
- `ControlKit.kt` helps. `Modifier.sliderInput` and `Modifier.holdInput` already satisfy the input and semantics rules.
  `BaselineControls` is a complete reference, and `examples/PaperTheme.kt` shows a second look.

## The interface (v1)

**Hookup:**
- `RoseTheme(theme: Theme, dark: Boolean = isSystemInDarkTheme()) { … }`.
- `RoseTheme.tokens`, `.colors`, `.type`, `.shapes`, `.spacing` and `.id` read the current theme.
- Runtime swaps keep the template's state, even across a theme that overrides `Provide`.

**Tokens:**

| Group | Values |
|---|---|
| `RoseColors` | `background`/`onBackground`, `surface`/`onSurface`, `muted`, `surfaceVariant`/`onSurfaceVariant`, `accent`/`onAccent`, `outline`, `danger`/`onDanger`, `isDark` |
| `RoseType` | `display`, `title`, `body`, `label`, `caption` |
| `RoseShapes` | `small` (controls), `medium` (cards), `large` (sheets) |
| `RoseSpacing` | `xs`, `s`, `m`, `l`, `xl`, `xxl` |
| other | `minTouchTarget` |

**Stock controls** (template call → what the theme must keep):

| Control | Template calls | The theme must keep |
|---|---|---|
| `Screen` | `Screen(title, navigation?, actions, bottomBar?) { padding -> }` | heading title; bars as icon buttons; handles system bars, passes content padding |
| `Panel` | `Panel(kind = Raised/Flat/Inset, onClick?) { }` | one clickable element when `onClick`; sets content colour; owns inner padding |
| `Button` | `Button(label, onClick, kind = Primary/Secondary/Quiet/Danger, icon?, enabled)` | Role.Button, text, one click per tap, disabled |
| `IconButton` | `IconButton(icon, contentDescription, onClick, checked?, prominent)` | Role.Button, or a toggle when `checked != null` |
| `Toggle` | `Toggle(checked, onCheckedChange, label)` | one Role.Switch element with its label |
| `Slider` | `Slider(value, onValueChange, label, valueRange, steps, onValueChangeFinished, valueText?, highlight?, marks)` | tap/drag anywhere sets the value; progress semantics + set-progress. `highlight` (loop passage) and `marks` are display only. This is the player's timeline. |
| `Progress` | `Progress(progress or null, label)` | determinate/indeterminate progress semantics |
| `ListItem` | `ListItem(headline, supporting?, leadingIcon?/leading?, trailing?, onClick?, selected)` | row clickable, trailing controls separate |
| `Segmented` | `Segmented(options, selectedIndex, onSelect, label)` | Role.Tab options with selected state |
| `TextField` | `TextField(value, onValueChange, label, placeholder?, singleLine, onSubmit?)` | editable text, IME Done calls `onSubmit` |
| `DirectionPad` | `DirectionPad(onDirection(dir, down), directions)` | hold/release per direction, independent fingers |
| `HoldButton` | `HoldButton(label, onPressedChange, icon?)` | press/release, works while the pad is held |
| `Stat` | `Stat(label, value, progress?)` | HUD/summary readout |
| others | `Icon(icon: RoseIcon, contentDescription)`, `Divider()`, `Text(text, role = TextRole.Body, color)` | |

`RoseIcon` has 24 icons, requested by meaning (Back, Play, Pause, Loop, Like, Up, Jump…). A theme may draw its own
artwork for each. `design/build/screenshots/icons.png` shows the baseline set.

**Evolving it:** new controls and props fields get defaults, so existing themes and templates keep compiling.
Props are classes, not positional interface methods, for that reason. Ask for anything a pilot needs that isn't here
rather than drawing a private control in a template; a private control would not change with the theme.

## Tests and what they prove

| Suite | Where | Runs |
|---|---|---|
| `ThemeConformanceTest` (20 checks; 2 render pixels with Robolectric's native graphics) | every theme's module, and `matrix` for all pilots | light + dark × API 34 + 37 |
| `<Template>BehaviorTest` | each template module, under Baseline and Paper | light + dark × API 34 + 37 |
| `<Template>Scenario` | `matrix`, under every pilot theme | light + dark × API 34 + 37 |
| `MatrixWiringTest` | `matrix`: 3 templates × (3 pilot themes + Baseline) | API 34 + 37 |
| `TallyBehaviorTest`, `ThemeSwapTest`, `PaperConformance` | `sample` | as above |

These are JVM tests on Robolectric, a virtual Android. They are not a device or emulator run. Physical-device and
emulator results for Android 14 and current Android belong to Wave 3, through Rose's real build path.

Known test-only exception: Espresso is pinned to `3.8.0-alpha04`, which only the tests use. The latest stable, 3.7.0,
calls `InputManager.getInstance()`, which API 37 removed, so every Compose test fails on API 37. Move to 3.8.0 when it
is stable.

## Versions

| Component | Version |
|---|---|
| Gradle (wrapper, checksum-pinned) | 9.9.1 |
| Android Gradle Plugin | 9.5.1 (built-in Kotlin) |
| Kotlin | 2.4.21, with the Compose compiler plugin |
| Compose BOM | 2026.09.00 (Compose UI 1.12.1) |
| activity-compose | 1.13.0 |
| compileSdk / targetSdk | 37 (Android 17) |
| minSdk | 23 (Android 6), the lowest the current Compose BOM supports |
| Tests | Robolectric 4.18.1, JUnit 4.13.2 |

All versions live in `gradle/libs.versions.toml`.

**Dependencies must build in Rose's builder.** Rose's builder (App Core) builds apps offline. It resolves plugins and
libraries only from its read-only cache, seeded from `rose/app_builder/gradle_stack.py`. That cache holds:
- Compose BOM 2026.09.00: ui, foundation, animation, material3, tooling-preview;
- activity-compose, core-ktx;
- lifecycle and viewmodel compose, navigation-compose, datastore-preferences;
- media3 exoplayer and ui-compose;
- coroutines, kotlinx serialization.

A template or theme may use those freely in `implementation`/`api`. Ask Home/App Product before adding any other
release dependency, so it can be added to the cache first. Test-only dependencies (Robolectric, Espresso, ui-test) are
not built there and need no ask. The other build rules:
- repositories stay `google()`, `mavenCentral()` and `gradlePluginPortal()`;
- no binary files besides the wrapper jar (no other `.jar`, `.aar`, `.so` or `.dex`);
- no signing configs or splits: the builder sets the app's identity and signs it.

## How Rose starts an app from a starter

A create request may name `starter: {"template": id, "theme": id}`, using the ids in `starters.json`. The builder
then writes the project's first tree with `rose.app_builder.starters.skeleton`. The tree contains:
- `app/`, whose `MainActivity` is `RoseTheme(<Theme>) { <Template>.App() }`;
- the vendored `:design`, `:design-testing` and `:examples` modules;
- the chosen template and theme, as source only;
- this catalog.

The writer then customizes inside the template. Without a starter, the builder's plain skeleton is unchanged.
`starters.json` must list exactly the pilots in `matrix/.../Pilots.kt`; `test_app_starters` checks that.

## Behaviour evidence across themes (`:evidence`)

The same template code must behave the same under every theme. A theme may change every pixel, but not what a
person can read and do. `ThemeInvarianceTest` runs each template under each pilot theme, on API 34 and 37:
- it checks invariants: a titled screen, every clickable labelled, touch targets of at least 48dp;
- it writes a normalized semantics snapshot to `evidence/build/evidence/<template>/<theme>-api<N>.txt`, of the
  opening screen and after clicking each control in turn. The snapshot holds texts, roles, actions and states,
  never sizes, positions, colours or layout order.

`compareThemeSnapshots`, part of `check`, fails when one template's snapshots differ between themes and writes
`report.json` either way.

```sh
./gradlew :evidence:compareThemeSnapshots                      # the pilot templates
./gradlew :evidence:compareThemeSnapshots \
  -PwrittenTemplate=<generated tree>/templates/<id> \
  -PtemplateObject=app.rose.template.<pkg>.<Object>            # a writer's template module (Wave 3)
```

This is Robolectric on the JVM, not a device run. It needs the network for Robolectric's Android jars, so it runs
in CI or a cloud session, not inside the builder's offline sandbox.

## Not here (other owners)

- App Core owns ordinary Gradle/Kotlin/Compose support in Rose's builder (build, sign, install, update).
- The backend builder owner owns the removal of the builder-managed Rust backend.
- Wave 3 (Home/App Product) connects these starters to Rose's real generation path: one fixed request per template,
  only the theme varies, all nine built and exercised. A gallery like `:matrix` is a development tool, not that
  validation.
