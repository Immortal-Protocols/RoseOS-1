package app.rose.theme.skeuomorphic

import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.focusable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import app.rose.design.RoseTheme
import app.rose.design.SliderProps
import app.rose.design.Text
import app.rose.design.ToggleProps
import app.rose.design.minTouchTarget
import app.rose.design.sliderFraction
import app.rose.design.sliderInput
import app.rose.design.snapSliderValue
import kotlin.math.abs
import kotlin.math.min

@Composable
internal fun SlideSwitch(p: ToggleProps) {
    val colors = RoseTheme.colors
    val interaction = remember { MutableInteractionSource() }
    val focused by interaction.collectIsFocusedAsState()
    val pressed by interaction.collectIsPressedAsState()
    val knob by animateDpAsState(if (p.checked) 29.dp else 3.dp, label = "switch travel")
    Row(
        p.modifier.fillMaxWidth().minTouchTarget()
            .toggleable(p.checked, interaction, null, enabled = p.enabled, role = Role.Switch, onValueChange = p.onCheckedChange)
            .disabledAppearance(p.enabled),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(p.label, Modifier.weight(1f).padding(end = 12.dp))
        val fill = if (p.checked) colors.accent else colors.surfaceVariant
        Box(
            Modifier.size(width = 58.dp, height = 30.dp).recessedWell(fill, colors.outline, CircleShape)
                .then(if (focused) Modifier.border(2.dp, colors.onBackground, CircleShape) else Modifier),
        ) {
            Box(
                Modifier.align(if (p.checked) Alignment.CenterStart else Alignment.CenterEnd).padding(horizontal = 10.dp)
                    .size(5.dp).background(if (p.checked) colors.onAccent else colors.outline, CircleShape),
            )
            Box(
                Modifier.offset { IntOffset(knob.roundToPx(), 3.dp.roundToPx()) }.size(26.dp, 24.dp)
                    .keycap(colors.surface, CircleShape, pressed = pressed, shallow = true),
                contentAlignment = Alignment.Center,
            ) {
                Column {
                    repeat(2) {
                        Box(Modifier.padding(vertical = 1.dp).size(8.dp, 1.dp).background(colors.outline))
                    }
                }
            }
        }
    }
}

@Composable
internal fun GroovedSlider(p: SliderProps) {
    val colors = RoseTheme.colors
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    val fraction = sliderFraction(p.value, p.valueRange)
    val interaction = remember { MutableInteractionSource() }
    val focused by interaction.collectIsFocusedAsState()
    var dragging by remember { mutableStateOf(false) }
    val inset = 15.dp
    // Observe the same gesture without consuming it. ControlKit owns track input and callbacks.
    val touchState = if (p.enabled) Modifier.pointerInput(Unit) {
        awaitEachGesture {
            val down = awaitFirstDown(requireUnconsumed = false, pass = PointerEventPass.Initial)
            dragging = true
            try {
                do {
                    val event = awaitPointerEvent(PointerEventPass.Initial)
                } while (event.changes.any { it.id == down.id && it.pressed })
            } finally {
                dragging = false
            }
        }
    } else Modifier
    val shape = RoseTheme.shapes.small
    Box(
        p.modifier.fillMaxWidth().minTouchTarget().sliderInput(p, thumbInset = inset).then(touchState)
            .onKeyEvent { event ->
                if (!p.enabled || event.type != KeyEventType.KeyDown) return@onKeyEvent false
                val range = p.valueRange
                val step = (range.endInclusive - range.start) / if (p.steps > 0) p.steps + 1 else 20
                val forward = if (rtl) -step else step
                val target = when (event.key) {
                    Key.DirectionLeft -> p.value - forward
                    Key.DirectionRight -> p.value + forward
                    Key.MoveHome -> range.start
                    Key.MoveEnd -> range.endInclusive
                    else -> return@onKeyEvent false
                }
                p.onValueChange(snapSliderValue(target, range, p.steps))
                p.onValueChangeFinished?.invoke()
                true
            }
            .focusable(enabled = p.enabled, interactionSource = interaction).disabledAppearance(p.enabled)
            .then(if (focused) Modifier.border(2.dp, colors.onBackground, shape) else Modifier),
    ) {
        Canvas(Modifier.fillMaxWidth().height(48.dp).align(Alignment.Center)) {
            val endSpace = min(inset.toPx(), size.width / 2)
            val span = size.width - endSpace * 2
            val y = center.y
            fun x(fraction: Float) = endSpace + span * if (rtl) 1f - fraction else fraction
            val track = 10.dp.toPx()
            val grooveTop = y - track / 2
            val grooveRadius = CornerRadius(track / 2)
            drawRoundRect(colors.outline, Offset(endSpace - 1.dp.toPx(), grooveTop - 1.dp.toPx()),
                Size(span + 2.dp.toPx(), track + 2.dp.toPx()), grooveRadius)
            drawRoundRect(
                Brush.verticalGradient(listOf(lerp(colors.surfaceVariant, Color.Black, 0.31f), colors.surfaceVariant), grooveTop, grooveTop + track),
                Offset(endSpace, grooveTop), Size(span, track), grooveRadius,
            )
            val activeEnd = x(fraction)
            if (fraction > 0f) {
                drawRoundRect(
                    Brush.verticalGradient(listOf(lerp(colors.accent, Color.Black, 0.10f), colors.accent), grooveTop, grooveTop + track),
                    Offset(min(x(0f), activeEnd), grooveTop), Size(span * fraction, track), grooveRadius,
                )
            }
            if (p.steps in 1..12) {
                for (step in 0..p.steps + 1) {
                    val tick = x(step.toFloat() / (p.steps + 1))
                    drawLine(colors.outline, Offset(tick, y + 8.dp.toPx()), Offset(tick, y + 11.dp.toPx()), 1.dp.toPx())
                }
            }
            p.highlight?.let { range ->
                val a = x(sliderFraction(range.start, p.valueRange))
                val b = x(sliderFraction(range.endInclusive, p.valueRange))
                val band = 4.dp.toPx()
                drawRoundRect(colors.accent, Offset(min(a, b), y - 16.dp.toPx()), Size(abs(b - a), band), CornerRadius(band / 2))
            }
            p.marks.forEach { mark ->
                val tick = x(sliderFraction(mark, p.valueRange))
                drawLine(colors.onBackground, Offset(tick, y - 18.dp.toPx()), Offset(tick, y - 8.dp.toPx()), 2.dp.toPx())
            }
            val knob = Offset(activeEnd, y + if (dragging) 1.dp.toPx() else 0f)
            val radius = 12.dp.toPx()
            drawCircle(Color.Black.copy(alpha = 0.35f), radius, knob.copy(y = knob.y + if (dragging) 1.dp.toPx() else 3.dp.toPx()))
            drawCircle(colors.outline, radius, knob)
            drawCircle(
                Brush.verticalGradient(
                    listOf(lerp(colors.surface, Color.White, 0.21f), lerp(colors.surface, Color.Black, if (dragging) 0.24f else 0.10f)),
                    knob.y - radius, knob.y + radius,
                ), radius - 1.dp.toPx(), knob,
            )
            for (notch in -1..1) {
                val grip = knob.x + (notch * 3).dp.toPx()
                drawLine(colors.onSurface, Offset(grip, knob.y - 4.dp.toPx()), Offset(grip, knob.y + 4.dp.toPx()), 1.dp.toPx())
            }
        }
    }
}

@Composable
internal fun Meter(progress: Float?, modifier: Modifier = Modifier) {
    val colors = RoseTheme.colors
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    val sweep = if (progress == null) {
        val animation by rememberInfiniteTransition(label = "meter").animateFloat(0f, 1f, infiniteRepeatable(tween(1200)), label = "sweep")
        animation
    } else 0f
    val shape = CircleShape
    Box(modifier.fillMaxWidth().height(10.dp).recessedWell(colors.surfaceVariant, colors.outline, shape).padding(1.dp).clip(shape)) {
        Canvas(Modifier.fillMaxSize()) {
            val length = progress?.coerceIn(0f, 1f) ?: 0.32f
            val start = if (progress == null) sweep * (1f + length) - length else 0f
            val left = if (rtl) 1f - start - length else start
            drawRect(
                Brush.verticalGradient(listOf(lerp(colors.accent, Color.White, 0.16f), colors.accent, lerp(colors.accent, Color.Black, 0.18f))),
                Offset(left * size.width, 0f), Size(length * size.width, size.height),
            )
        }
    }
}
