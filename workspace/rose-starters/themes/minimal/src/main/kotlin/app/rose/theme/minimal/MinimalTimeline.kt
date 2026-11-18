package app.rose.theme.minimal

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.border
import androidx.compose.foundation.focusable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import app.rose.design.ProgressProps
import app.rose.design.RoseTheme
import app.rose.design.SliderProps
import app.rose.design.minTouchTarget
import app.rose.design.sliderFraction
import app.rose.design.sliderInput
import app.rose.design.snapSliderValue

/** The time cursor, passage bracket above and position marks below occupy separate lanes. */
@Composable
internal fun MinimalSlider(p: SliderProps) {
    val c = RoseTheme.colors
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    val inset = 12.dp
    val activeInk = if (p.enabled) c.accent else c.muted
    val fraction = sliderFraction(p.value, p.valueRange)
    val passage = p.highlight?.takeUnless { it.isEmpty() }?.let {
        sliderFraction(it.start, p.valueRange)..sliderFraction(it.endInclusive, p.valueRange)
    }
    val marks = p.marks.map { sliderFraction(it, p.valueRange) }
    var focused by remember { mutableStateOf(false) }
    var touching by remember { mutableStateOf(false) }
    val interaction = if (p.enabled) {
        Modifier.onFocusChanged { focused = it.isFocused }
            .onKeyEvent { event ->
                if (event.type == KeyEventType.KeyDown) moveSliderWithKey(p, event.key, rtl) else false
            }
            .focusable()
            // Observe at Initial so this visual state doesn't compete with sliderInput for events.
            .pointerInput(Unit) {
                awaitEachGesture {
                    val pointer = awaitFirstDown(requireUnconsumed = false, pass = PointerEventPass.Initial).id
                    touching = true
                    try {
                        do {
                            val event = awaitPointerEvent(PointerEventPass.Initial)
                        } while (event.changes.any { it.id == pointer && it.pressed })
                    } finally {
                        touching = false
                    }
                }
            }
    } else {
        Modifier
    }
    var frame = p.modifier.fillMaxWidth().minTouchTarget().sliderInput(p, thumbInset = inset).then(interaction)
    if (focused && p.enabled) frame = frame.border(2.dp, c.accent, RoseTheme.shapes.small)
    // The input and drawing use the same measured area, including when only a minimum height is specified.
    Box(frame.drawBehind {
        val edge = inset.toPx().coerceAtMost(size.width / 2f)
        val width = size.width - 2f * edge
        val center = size.height / 2f
        fun x(f: Float) = edge + (if (rtl) 1f - f else f) * width

        drawLine(c.outline, Offset(x(0f), center), Offset(x(1f), center), 1.dp.toPx())
        drawLine(activeInk, Offset(x(0f), center), Offset(x(fraction), center), 3.dp.toPx())
        passage?.let {
            val y = center - 11.dp.toPx()
            val depth = 4.dp.toPx()
            val line = 2.dp.toPx()
            val start = x(it.start)
            val end = x(it.endInclusive)
            drawLine(activeInk, Offset(start, y), Offset(end, y), line)
            drawLine(activeInk, Offset(start, y), Offset(start, y + depth), line)
            drawLine(activeInk, Offset(end, y), Offset(end, y + depth), line)
        }
        marks.forEach { mark ->
            val x = x(mark)
            drawLine(c.onBackground, Offset(x, center + 6.dp.toPx()), Offset(x, center + 12.dp.toPx()), 1.dp.toPx())
        }

        val thumbHeight = 18.dp.toPx()
        val thumbWidth = (if (p.enabled && touching) 6.dp else 3.dp).toPx()
        val gap = 2.dp.toPx()
        val thumbLeft = x(fraction) - thumbWidth / 2f
        val thumbTop = center - thumbHeight / 2f
        drawRect(c.background, Offset(thumbLeft - gap, thumbTop), Size(thumbWidth + 2f * gap, thumbHeight))
        drawRect(activeInk, Offset(thumbLeft, thumbTop), Size(thumbWidth, thumbHeight))
    })
}

private fun moveSliderWithKey(p: SliderProps, key: Key, rtl: Boolean): Boolean {
    val range = p.valueRange
    val parts = if (p.steps > 0) p.steps.toFloat() + 1f else 20f
    val increment = (range.endInclusive - range.start) / parts
    val horizontalStep = if (rtl) -increment else increment
    val candidate = when (key) {
        Key.DirectionLeft -> p.value - horizontalStep
        Key.DirectionRight -> p.value + horizontalStep
        Key.MoveHome -> range.start
        Key.MoveEnd -> range.endInclusive
        else -> return false
    }
    val next = snapSliderValue(candidate, range, p.steps)
    if (next != p.value) {
        p.onValueChange(next)
        p.onValueChangeFinished?.invoke()
    }
    return true
}

@Composable
internal fun MinimalProgress(p: ProgressProps) {
    val fraction = p.progress?.coerceIn(0f, 1f)
    MinimalProgressBar(
        fraction,
        p.modifier.semantics {
            contentDescription = p.label
            progressBarRangeInfo = fraction?.let { ProgressBarRangeInfo(it, 0f..1f) }
                ?: ProgressBarRangeInfo.Indeterminate
        },
    )
}

@Composable
internal fun MinimalProgressBar(fraction: Float?, modifier: Modifier) {
    val c = RoseTheme.colors
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    val sweep = if (fraction == null) {
        rememberInfiniteTransition(label = "minimal progress").animateFloat(
            initialValue = -0.25f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(tween(durationMillis = 1050, easing = LinearEasing)),
            label = "sweep",
        )
    } else {
        null
    }
    Canvas(modifier.fillMaxWidth().height(4.dp)) {
        val y = size.height / 2f
        val start = sweep?.value ?: 0f
        val end = fraction ?: (start + 0.25f).coerceAtMost(1f)
        fun x(f: Float) = if (rtl) size.width * (1f - f) else size.width * f

        drawLine(c.outline, Offset(0f, y), Offset(size.width, y), 1.dp.toPx())
        if (end > 0f) {
            drawLine(c.accent, Offset(x(start.coerceAtLeast(0f)), y), Offset(x(end), y), size.height)
        }
    }
}
