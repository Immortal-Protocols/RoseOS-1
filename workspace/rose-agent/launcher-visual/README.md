# Launcher visual cards

The native launcher accepts one private, bounded, declarative card over the existing authenticated phone command channel. Supported native cards are meal/nutrition snapshots and schematic wind-flow explainers. This is an optional home-screen card, not another app or an OS modification. It does not execute markup or fetch data; no automatic A-List or weather synchronization is enabled.

## Publishing a requested meal view

Use this when the user asks for meals or nutrition on the home screen. It is an optional tool, not a restriction on other ways to answer. This renderer currently supports meal/nutrition data, not arbitrary charts or HTML. Do not silently change a requested different visualization into meal data.

Prepare a UTF-8 JSON file outside the repository (for example under `~/.config/pixel-voice/launcher-visual/`, directory mode 700 and file mode 600). Read the requested records from the actual authorized source; do not reuse an old snapshot unless that is what was requested. Example structure below uses synthetic values only:

```json
{"id":"sample-meals","title":"Meals","subtitle":"Oct 12 · logged food","source":"Source name","updatedAt":"2026-10-12T20:00:00Z","days":[{"date":"2026-10-12","label":"Oct 12","meals":[{"id":"breakfast","name":"Breakfast","estimated":true,"notes":"Portions estimated","nutrients":[{"name":"Energy","value":400,"unit":"kcal","group":"macro"},{"name":"Protein","value":20,"unit":"g","group":"macro"},{"name":"Calcium","value":150,"unit":"mg","group":"micro"}]}]}]}
```

The file is limited to 100 KB, 14 days, 50 meals total, and 50 nutrients per meal. Use short labels. Values must be finite nonnegative numbers. Preserve source units; absent values are unknown, not zero. Use consistent nutrient names and units for totals. Mark estimates, describe partial coverage as logged food, and omit uncertain consumption times. `group` is `macro` or `micro`; optional `time` uses `HH:mm`.

## Wind-flow card

For a requested wind explanation, use `kind: "wind"` with a compact declarative description. The app draws a native inland-to-coast cross-section from these text fields; it does not derive or fetch weather data. Include the issuing source and issue time so a forecast snapshot cannot look live. Example values below are illustrative, not a current forecast:

```json
{"id":"wind-example","kind":"wind","title":"Offshore wind setup","subtitle":"Forecast snapshot","source":"Forecast office","updatedAt":"2026-10-13T19:57:00-07:00","wind":{"origin":"Inland high pressure","barrier":"Across the mountains","destination":"Down toward valleys and coast","direction":"Northeast / offshore","effect":"Descending air compresses, warms and dries.","peak":"Strongest Thursday–Friday","caution":"Check local advisories."}}
```

`wind` requires `origin`, `barrier`, `destination`, `direction`, `effect`, and `peak` text fields, with short bounded labels; `caution` is optional. Publishing any card replaces the one current card. It leaves the user's Visual cards switch as set, so a disabled switch stays disabled.

Publishing replaces the current card. Do not print the full private payload. A successful result reports `status: ok`, the stored ID/counts and visibility. If `enabled` is false, respect the user's off switch rather than claiming the card is visible or re-enabling it. If available, inspect the launcher and confirm the intended card before claiming it is displayed. Delivery needs the existing paired phone connection; it does not require an APK rebuild. The phone shows the saved snapshot until it is replaced or cleared.

Publish a private local JSON file:

    python3 rose-agent/launcher-visual/control.py set /absolute/private/path/visual.json

Read compact status or remove the current card:

    python3 rose-agent/launcher-visual/control.py status
    python3 rose-agent/launcher-visual/control.py clear

The publisher intentionally excludes the submitted payload from its output. Keep real meal records outside this repository. The phone stores the current card privately. Missing nutrients must stay missing; estimates remain labelled; totals cover supplied logged food only and are not complete daily intake or dietary advice.

## Revert

Use the launcher’s Home options to disable the card area, or `clear` above to remove its content. This leaves the voice connection, Bluetooth, audio routing, worker shortcuts, and conversation untouched.

The baseline source and signed version-118 APK are saved under the private rollback location recorded in `experiments/opus-home-visual/baseline.json`. The isolated experiment repository contains the code diff against baseline commit `f5d0f08`. For a full code revert, restore only those changed launcher/phone-command files and rebuild using a **higher** Android version code; do not overwrite unrelated newer fixes. A package downgrade to the saved APK may be refused by Android and is not the normal rollback path. Never uninstall the app just to downgrade: that would erase its private state.

## Checks

    node --test rose-agent/launcher-visual/commands.test.mjs

Device review must cover real payload delivery, meal macro/micro units and wind-flow rendering, theme contrast, scrolling, dismissal/pinning/master-off, unchanged mic/end bounds, and the normal Home entry point.
