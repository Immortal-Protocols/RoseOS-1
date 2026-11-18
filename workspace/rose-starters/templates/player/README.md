# Player starter

```kotlin
RoseTheme(theme) { PlayerTemplate.App() }
```

`PlayerTemplate` plays the bundled 24-second **Between Stations** synth clip offline through Android `MediaPlayer`. Drag or tap the timeline to scrub, or use its accessibility progress action. Use **Set A** and **Set B** to mark the current playhead. A passage must be at least one second long. **Loop passage** repeats that range. Scrubbing outside an active passage turns looping off. Playback pauses when the screen goes into the background or loses audio focus. The playhead and passage survive screen recreation.

The recording and arrangement are original to this repository and use its MIT license. The module’s `generatePlayerClip` task builds the PCM WAV from its score and bundles it as an Android resource. The waveform uses the clip’s RMS envelope. Playback needs no extra release dependencies or network permission.

`PlayerTemplateBehaviorTest` exercises the UI and Android media transport in Robolectric. `PlayerScreenshots` writes images of an armed passage to `build/screenshots/`. `PlayerScenario` runs the same interaction in the matrix for each implemented pilot theme.
