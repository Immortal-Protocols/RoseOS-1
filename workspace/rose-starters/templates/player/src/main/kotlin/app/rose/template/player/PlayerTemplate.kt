package app.rose.template.player

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.unit.LayoutDirection
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import app.rose.design.Button
import app.rose.design.ButtonKind
import app.rose.design.Panel
import app.rose.design.PanelKind
import app.rose.design.RoseIcon
import app.rose.design.RoseTheme
import app.rose.design.Screen
import app.rose.design.Slider
import app.rose.design.Template
import app.rose.design.Text
import app.rose.design.TextRole
import app.rose.design.Toggle
import kotlin.math.roundToInt

/** An offline audio player with a scrubbable timeline and a repeatable A–B passage. */
object PlayerTemplate : Template {
    override val id = "player"
    override val displayName = "Player"
    override val implemented = true

    @Composable
    override fun App(modifier: Modifier) {
        val state = rememberSaveable(saver = PlayerState.Saver) { PlayerState() }
        val context = LocalContext.current.applicationContext
        val lifecycle = LocalLifecycleOwner.current.lifecycle
        var playback by remember { mutableStateOf<PlayerPlayback?>(null) }
        DisposableEffect(context, lifecycle, state) {
            val session = PlayerPlayback(context, state)
            playback = session
            val observer = LifecycleEventObserver { _, event ->
                if (event == Lifecycle.Event.ON_PAUSE) session.pause()
            }
            lifecycle.addObserver(observer)
            onDispose {
                lifecycle.removeObserver(observer)
                session.close()
                playback = null
            }
        }

        val spacing = RoseTheme.spacing
        Screen("Player", modifier) { insets ->
            Column(
                Modifier.fillMaxSize().padding(insets).verticalScroll(rememberScrollState()).padding(spacing.l),
                verticalArrangement = Arrangement.spacedBy(spacing.l),
            ) {
                Timeline(state, playback)
                PassageControls(state, playback)
            }
        }
    }
}

@Composable
private fun Timeline(state: PlayerState, playback: PlayerPlayback?) {
    val spacing = RoseTheme.spacing
    val duration = playback?.duration ?: 0
    val ready = playback?.ready == true
    val playing = playback?.playing == true
    val current = if (ready) state.position else 0
    Panel(Modifier.fillMaxWidth()) {
        Column(verticalArrangement = Arrangement.spacedBy(spacing.m)) {
            Text("Between Stations", role = TextRole.Title)
            Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween, Alignment.Bottom) {
                Text(timeText(current), role = TextRole.Display)
                Text(timeText(duration), role = TextRole.Label, color = RoseTheme.colors.muted)
            }
            Waveform(state, duration)
            Slider(
                value = current.toFloat(),
                onValueChange = { playback?.scrub(it.roundToInt()) },
                label = "Playback timeline",
                modifier = Modifier.fillMaxWidth(),
                valueRange = 0f..duration.coerceAtLeast(1).toFloat(),
                enabled = ready,
                valueText = "${timeText(current)} of ${timeText(duration)}",
                highlight = if (ready && state.loop) state.start.toFloat()..state.end.toFloat() else null,
                marks = if (ready) listOf(state.start.toFloat(), state.end.toFloat()) else emptyList(),
            )
            Button(
                label = if (playing) "Pause" else "Play",
                onClick = { if (playing) playback?.pause() else playback?.play() },
                modifier = Modifier.fillMaxWidth(),
                enabled = ready,
                kind = ButtonKind.Primary,
                icon = if (playing) RoseIcon.Pause else RoseIcon.Play,
            )
            playback?.message?.let { Text(it, role = TextRole.Body) }
        }
    }
}

@Composable
private fun PassageControls(state: PlayerState, playback: PlayerPlayback?) {
    val spacing = RoseTheme.spacing
    val ready = playback?.ready == true
    Panel(Modifier.fillMaxWidth(), kind = PanelKind.Inset) {
        Column(verticalArrangement = Arrangement.spacedBy(spacing.m)) {
            Toggle(
                checked = state.loop,
                onCheckedChange = { playback?.setLoop(it) },
                label = "Loop passage",
                modifier = Modifier.fillMaxWidth(),
                enabled = ready,
            )
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(spacing.s)) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(spacing.xs)) {
                    Text("Start ${timeText(state.start)}", role = TextRole.Label)
                    Button("Set A", { playback?.setStart() }, Modifier.fillMaxWidth(),
                        enabled = ready && state.position <= state.end - MINIMUM_PASSAGE_MILLIS)
                }
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(spacing.xs)) {
                    Text("End ${timeText(state.end)}", role = TextRole.Label)
                    Button("Set B", { playback?.setEnd() }, Modifier.fillMaxWidth(),
                        enabled = ready && state.position >= state.start + MINIMUM_PASSAGE_MILLIS)
                }
            }
        }
    }
}

/** The fixture's RMS envelope is artwork. All timeline input goes through the stock slider. */
@Composable
private fun Waveform(state: PlayerState, duration: Int) {
    val colors = RoseTheme.colors
    val spacing = RoseTheme.spacing
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    Canvas(Modifier.fillMaxWidth().height(spacing.xxl * 3)) {
        val step = size.width / ENVELOPE.size
        val width = step * 0.58f
        for ((index, sample) in ENVELOPE.withIndex()) {
            val time = (index + 0.5f) / ENVELOPE.size * duration
            val x = if (rtl) size.width - (index + 0.5f) * step else (index + 0.5f) * step
            val height = size.height * sample / 100f
            val color = when {
                state.loop && time >= state.start && time < state.end -> colors.accent
                time < state.position -> colors.onSurface
                else -> colors.outline
            }
            drawRoundRect(color, Offset(x - width / 2, (size.height - height) / 2), Size(width, height),
                CornerRadius(width / 2))
        }
    }
}

private fun timeText(millis: Int): String {
    val seconds = millis / 1_000
    return "${seconds / 60}:${(seconds % 60).toString().padStart(2, '0')}"
}

// RMS amplitudes from the Between Stations score in build.gradle.kts.
private val ENVELOPE = intArrayOf(
    72, 37, 20, 59, 40, 24, 80, 42, 23, 68, 46, 28,
    92, 47, 26, 76, 51, 31, 78, 42, 23, 67, 45, 27,
    91, 49, 27, 78, 53, 32, 100, 51, 28, 83, 56, 34,
    83, 43, 23, 68, 46, 28, 91, 47, 25, 75, 51, 30,
    79, 41, 22, 65, 44, 27, 88, 45, 25, 73, 49, 29,
    72, 37, 20, 59, 40, 24, 63, 32, 17, 52, 35, 21,
)
