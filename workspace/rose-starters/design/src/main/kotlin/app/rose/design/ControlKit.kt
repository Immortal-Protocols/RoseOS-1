package app.rose.design

import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.waitForUpOrCancellation
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChange
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.disabled
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.setProgress
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import kotlin.math.roundToInt

// Helpers for theme authors. They already satisfy the input and semantics parts of the Controls contract, so a theme
// can use them and only draw. A theme is free not to (the Material 3 theme wraps Material's own controls instead).

/** Sets [LocalContentColor] for [content]: what a surface, button or panel does for the text and icons inside it. */
@Composable
fun ProvideContentColor(color: Color, content: @Composable () -> Unit) =
    CompositionLocalProvider(LocalContentColor provides color, content = content)

/** At least the theme's minimum touch target in both directions. */
@Composable
fun Modifier.minTouchTarget(): Modifier {
    val min = RoseTheme.tokens.minTouchTarget
    return defaultMinSize(minWidth = min, minHeight = min)
}

/** [value] clamped into [range] and, with [steps] > 0, snapped to one of the steps + 2 evenly spaced positions. */
fun snapSliderValue(value: Float, range: ClosedFloatingPointRange<Float>, steps: Int): Float {
    val clamped = value.coerceIn(range.start, range.endInclusive)
    if (steps <= 0 || range.endInclusive == range.start) return clamped
    val segments = steps + 1
    val fraction = (clamped - range.start) / (range.endInclusive - range.start)
    return range.start + (fraction * segments).roundToInt().toFloat() / segments * (range.endInclusive - range.start)
}

/** Where [value] sits in [range], 0..1. */
fun sliderFraction(value: Float, range: ClosedFloatingPointRange<Float>): Float =
    if (range.endInclusive == range.start) 0f else ((value - range.start) / (range.endInclusive - range.start)).coerceIn(0f, 1f)

/**
 * The whole input and semantics side of a [SliderProps]: a tap or a horizontal drag anywhere on this element sets the
 * value at that position (between [thumbInset] from each end, which is where the theme draws the ends of its track),
 * and the accessibility contract (description, state, progress range, set-progress, disabled). Right-to-left aware.
 * Draw the track and thumb inside this element from `sliderFraction(p.value, p.valueRange)`.
 */
@Composable
fun Modifier.sliderInput(p: SliderProps, thumbInset: Dp = 0.dp): Modifier {
    val props by rememberUpdatedState(p)
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    val insetPx = with(LocalDensity.current) { thumbInset.toPx() }
    fun valueAt(x: Float, width: Int): Float {
        val usable = (width - 2 * insetPx).coerceAtLeast(1f)
        var f = ((x - insetPx) / usable).coerceIn(0f, 1f)
        if (rtl) f = 1f - f
        val r = props.valueRange
        return snapSliderValue(r.start + f * (r.endInclusive - r.start), r, props.steps)
    }
    val input = if (!p.enabled) Modifier else Modifier.pointerInput(rtl, insetPx) {
        awaitEachGesture {
            val down = awaitFirstDown(requireUnconsumed = false)
            down.consume()
            props.onValueChange(valueAt(down.position.x, size.width))
            var pointer = down.id
            while (true) {
                val event = awaitPointerEvent()
                val change = event.changes.firstOrNull { it.id == pointer } ?: event.changes.first().also { pointer = it.id }
                if (!change.pressed) {
                    change.consume()
                    break
                }
                if (change.positionChange() != androidx.compose.ui.geometry.Offset.Zero) {
                    change.consume()
                    props.onValueChange(valueAt(change.position.x, size.width))
                }
            }
            props.onValueChangeFinished?.invoke()
        }
    }
    return this
        .semantics {
            contentDescription = p.label
            p.valueText?.let { stateDescription = it }
            progressBarRangeInfo = ProgressBarRangeInfo(p.value.coerceIn(p.valueRange.start, p.valueRange.endInclusive), p.valueRange, p.steps)
            if (!p.enabled) disabled()
            else setProgress { target ->
                val snapped = snapSliderValue(target, props.valueRange, props.steps)
                props.onValueChange(snapped)
                props.onValueChangeFinished?.invoke()
                true
            }
        }
        .then(input)
}

/**
 * Press-and-hold input for [HoldButtonProps] and each [DirectionPadProps] direction: down calls
 * `onPressedChange(true)`, up or cancel calls `onPressedChange(false)`, independent of other fingers on other elements.
 * Replaces this element's semantics with one Role.Button described by [label], whose accessibility click is a press
 * then a release. Track your own pressed state from the callback to draw it.
 */
@Composable
fun Modifier.holdInput(label: String, enabled: Boolean, onPressedChange: (Boolean) -> Unit): Modifier {
    val callback by rememberUpdatedState(onPressedChange)
    val input = if (!enabled) Modifier else Modifier.pointerInput(Unit) {
        awaitEachGesture {
            val down = awaitFirstDown(requireUnconsumed = false)
            down.consume()
            callback(true)
            try {
                waitForUpOrCancellation()?.consume()
            } finally {
                callback(false)
            }
        }
    }
    return this
        .clearAndSetSemantics {
            contentDescription = label
            role = Role.Button
            if (!enabled) disabled()
            else onClick { callback(true); callback(false); true }
        }
        .then(input)
}
