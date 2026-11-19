package app.rose.design

import androidx.compose.runtime.Immutable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/**
 * Colour roles. A template asks for a role ("the accent", "muted text"), never a hex value, so every theme can answer
 * differently in light and dark mode. Each `onX` is the content colour that must stay readable on `x`
 * (ThemeConformance checks the contrast).
 */
@Immutable
class RoseColors(
    /** The window behind everything. */
    val background: Color,
    val onBackground: Color,
    /** Cards, panels, list rows, the top bar. */
    val surface: Color,
    val onSurface: Color,
    /** Secondary text and icons on [surface] or [background]: captions, timestamps, inactive tabs. */
    val muted: Color,
    /** A recessed or alternate surface: a track, a well, a selected row. */
    val surfaceVariant: Color,
    val onSurfaceVariant: Color,
    /** The one emphasis colour: primary buttons, progress, the selected option, the slider's active part. */
    val accent: Color,
    val onAccent: Color,
    /** Borders, dividers, unselected outlines. */
    val outline: Color,
    /** Destructive actions and errors. */
    val danger: Color,
    val onDanger: Color,
    val isDark: Boolean,
)

/** Text roles, largest to smallest. */
@Immutable
class RoseType(
    val display: TextStyle,
    val title: TextStyle,
    val body: TextStyle,
    val label: TextStyle,
    val caption: TextStyle,
)

/** Corner (or any other) shapes: [small] for controls, [medium] for cards and panels, [large] for sheets and dialogs. */
@Immutable
class RoseShapes(
    val small: Shape,
    val medium: Shape,
    val large: Shape,
)

/** A spacing scale. Templates lay out with these, so a dense theme and an airy one both look intended. */
@Immutable
class RoseSpacing(
    val xs: Dp = 4.dp,
    val s: Dp = 8.dp,
    val m: Dp = 12.dp,
    val l: Dp = 16.dp,
    val xl: Dp = 24.dp,
    val xxl: Dp = 32.dp,
)

/** Everything a template may read about appearance, for one theme in one mode (light or dark). */
@Immutable
class RoseTokens(
    val colors: RoseColors,
    val type: RoseType,
    val shapes: RoseShapes,
    val spacing: RoseSpacing = RoseSpacing(),
    /** Every interactive control's touch target is at least this big in both directions (never below 48dp). */
    val minTouchTarget: Dp = 48.dp,
)
