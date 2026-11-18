package app.rose.theme.material3

import androidx.compose.foundation.focusable
import androidx.compose.foundation.hoverable
import androidx.compose.foundation.indication
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.ripple
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.disabled
import androidx.compose.ui.semantics.focused
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.requestFocus
import androidx.compose.ui.semantics.setProgress
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import app.rose.design.Direction
import app.rose.design.DirectionPadProps
import app.rose.design.HoldButtonProps
import app.rose.design.RoseIcon
import app.rose.design.RoseIcons
import app.rose.design.RoseTheme
import app.rose.design.SliderProps
import app.rose.design.holdInput
import app.rose.design.minTouchTarget
import app.rose.design.sliderFraction
import app.rose.design.snapSliderValue

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun MaterialTimeline(p: SliderProps) {
    val scheme = MaterialTheme.colorScheme
    val minTarget = RoseTheme.tokens.minTouchTarget
    val markerColor = if (p.enabled) scheme.tertiary else scheme.onSurface.copy(alpha = 0.38f)
    val interactions = remember { MutableInteractionSource() }
    val focusRequester = remember { FocusRequester() }
    val isFocused by interactions.collectIsFocusedAsState()
    val colors = SliderDefaults.colors(thumbColor = if (isFocused) scheme.onPrimaryContainer else scheme.primary)
    val trackModifier = Modifier.drawWithContent {
        drawContent()
        fun timelineX(value: Float): Float {
            val fraction = sliderFraction(value, p.valueRange)
            return size.width * if (layoutDirection == LayoutDirection.Rtl) 1f - fraction else fraction
        }
        // Annotate above the track so marks stay visible on both its active and inactive segments.
        val markerY = -4.dp.toPx()
        p.highlight?.let { passage ->
            val start = maxOf(passage.start, p.valueRange.start)
            val end = minOf(passage.endInclusive, p.valueRange.endInclusive)
            if (end > start) {
                drawLine(
                    markerColor, Offset(timelineX(start), markerY), Offset(timelineX(end), markerY),
                    strokeWidth = 2.dp.toPx(), cap = StrokeCap.Round,
                )
            }
        }
        p.marks.forEach { mark ->
            if (mark in p.valueRange) {
                drawCircle(markerColor, radius = 2.dp.toPx(), center = Offset(timelineX(mark), markerY))
            }
        }
    }

    // Match the semantic bounds to the actual hit area. Material expands its internal TalkBack box past the track.
    Box(p.modifier.fillMaxWidth().minTouchTarget().clearAndSetSemantics {
        contentDescription = p.label
        progressBarRangeInfo = ProgressBarRangeInfo(
            p.value.coerceIn(p.valueRange.start, p.valueRange.endInclusive), p.valueRange, p.steps,
        )
        p.valueText?.let { stateDescription = it }
        if (p.enabled) {
            focused = isFocused
            requestFocus { focusRequester.requestFocus() }
            setProgress { target ->
                p.onValueChange(snapSliderValue(target, p.valueRange, p.steps))
                p.onValueChangeFinished?.invoke()
                true
            }
        } else {
            disabled()
        }
    }) {
        Slider(
            value = p.value,
            onValueChange = p.onValueChange,
            modifier = Modifier.fillMaxWidth().focusRequester(focusRequester),
            enabled = p.enabled,
            onValueChangeFinished = p.onValueChangeFinished,
            colors = colors,
            interactionSource = interactions,
            valueRange = p.valueRange,
            steps = p.steps,
            thumb = {
                // Material measures the slider's hit height from the thumb's slot.
                Box(Modifier.heightIn(min = minTarget), contentAlignment = Alignment.Center) {
                    SliderDefaults.Thumb(interactions, colors = colors, enabled = p.enabled)
                }
            },
            track = { state ->
                SliderDefaults.Track(
                    sliderState = state, colors = colors, enabled = p.enabled, modifier = trackModifier,
                )
            },
        )
    }
}

@Composable
internal fun MaterialDirectionPad(p: DirectionPadProps) {
    val keySize = RoseTheme.tokens.minTouchTarget
    val space = RoseTheme.spacing.xs
    // These are physical movement directions, so the pad keeps its left/right mapping in RTL.
    CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Ltr) {
        if (Direction.Up in p.directions || Direction.Down in p.directions) {
            Box(p.modifier.size(keySize * 3 + space * 2)) {
                if (Direction.Up in p.directions) {
                    DirectionKey(p, Direction.Up, RoseIcon.Up, Modifier.align(Alignment.TopCenter))
                }
                if (Direction.Left in p.directions) {
                    DirectionKey(p, Direction.Left, RoseIcon.Left, Modifier.align(Alignment.CenterStart))
                }
                if (Direction.Right in p.directions) {
                    DirectionKey(p, Direction.Right, RoseIcon.Right, Modifier.align(Alignment.CenterEnd))
                }
                if (Direction.Down in p.directions) {
                    DirectionKey(p, Direction.Down, RoseIcon.Down, Modifier.align(Alignment.BottomCenter))
                }
            }
        } else {
            Row(p.modifier, horizontalArrangement = Arrangement.spacedBy(space)) {
                if (Direction.Left in p.directions) DirectionKey(p, Direction.Left, RoseIcon.Left)
                if (Direction.Right in p.directions) DirectionKey(p, Direction.Right, RoseIcon.Right)
            }
        }
    }
}

@Composable
private fun DirectionKey(p: DirectionPadProps, direction: Direction, icon: RoseIcon, modifier: Modifier = Modifier) {
    HoldSurface(
        modifier.size(RoseTheme.tokens.minTouchTarget), direction.label, p.enabled,
        primary = false, onPressedChange = { p.onDirection(direction, it) },
    ) {
        Icon(RoseIcons.vector(icon), contentDescription = null)
    }
}

@Composable
internal fun MaterialHoldButton(p: HoldButtonProps) {
    HoldSurface(p.modifier, p.label, p.enabled, primary = true, onPressedChange = p.onPressedChange) {
        Row(
            Modifier.padding(horizontal = RoseTheme.spacing.l, vertical = RoseTheme.spacing.s),
            horizontalArrangement = Arrangement.spacedBy(RoseTheme.spacing.s),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            p.icon?.let { Icon(RoseIcons.vector(it), null, Modifier.size(18.dp)) }
            Text(p.label, style = MaterialTheme.typography.labelLarge)
        }
    }
}

/** A Material state layer over Rose's independent press/release input. */
@Composable
private fun HoldSurface(
    modifier: Modifier,
    label: String,
    enabled: Boolean,
    primary: Boolean,
    onPressedChange: (Boolean) -> Unit,
    content: @Composable () -> Unit,
) {
    val scheme = MaterialTheme.colorScheme
    val interactions = remember { MutableInteractionSource() }
    val callback by rememberUpdatedState(onPressedChange)
    var pointerHeld by remember { mutableStateOf(false) }
    var keyHeld by remember { mutableStateOf(false) }
    fun updateHold(fromPointer: Boolean, held: Boolean) {
        val wasPressed = pointerHeld || keyHeld
        if (fromPointer) pointerHeld = held else keyHeld = held
        val pressed = pointerHeld || keyHeld
        if (pressed != wasPressed) callback(pressed)
    }
    DisposableEffect(enabled) {
        onDispose { updateHold(fromPointer = false, held = false) }
    }

    val pressed = pointerHeld || keyHeld
    val container = when {
        !enabled -> scheme.onSurface.copy(alpha = 0.12f)
        pressed -> if (primary) scheme.primary else scheme.secondary
        primary -> scheme.primaryContainer
        else -> scheme.secondaryContainer
    }
    val foreground = when {
        !enabled -> scheme.onSurface.copy(alpha = 0.38f)
        pressed -> if (primary) scheme.onPrimary else scheme.onSecondary
        primary -> scheme.onPrimaryContainer
        else -> scheme.onSecondaryContainer
    }
    Surface(
        modifier = modifier.minTouchTarget()
            .holdInput(label, enabled) { updateHold(fromPointer = true, held = it) }
            .onFocusChanged { if (!it.isFocused) updateHold(fromPointer = false, held = false) }
            .onKeyEvent { event ->
                if (enabled && (event.key == Key.Enter || event.key == Key.DirectionCenter || event.key == Key.Spacebar)) {
                    updateHold(fromPointer = false, held = event.type == KeyEventType.KeyDown)
                    true
                } else {
                    false
                }
            }
            .focusable(enabled, interactions)
            .hoverable(interactions, enabled),
        color = container,
        contentColor = foreground,
        shape = if (primary) MaterialTheme.shapes.extraLarge else RoseTheme.shapes.small,
    ) {
        Box(
            Modifier.indication(interactions, ripple(color = foreground)),
            contentAlignment = Alignment.Center,
        ) { content() }
    }
}
