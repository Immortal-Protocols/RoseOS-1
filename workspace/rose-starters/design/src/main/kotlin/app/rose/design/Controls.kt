package app.rose.design

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Stable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/**
 * The stock controls every theme implements, the only controls a template uses. Templates call the top-level
 * functions in Stock.kt (`Button(…)`, `Slider(…)`, …). Each one forwards a props object to the current theme's
 * implementation here.
 *
 * **What a theme owns:** every pixel, shape, motion and press/hover/focus/drag state of the control.
 * **What a theme must keep** (ThemeConformance checks it for every theme in light and dark mode):
 *  - apply [Props.modifier] to the control's outermost element (templates and tests tag and size controls through it);
 *  - call each callback exactly when the props say (a click is one `onClick`, a drag reports values in the range);
 *  - expose the semantics each props class documents (role, text, state, progress, disabled), so a template's
 *    behaviour, and its tests, are the same under every theme;
 *  - show the caller's text as given (restyle it, never rewrite it: no upper-casing, truncating a label, or
 *    replacing it with an icon), because people and tests find controls by it;
 *  - give every interactive element a touch target of at least [RoseTokens.minTouchTarget];
 *  - read colours from the tokens it returned, so light and dark mode both work.
 *
 * Every v1 member is abstract, so a theme that compiles has hooked up every control. Members added later come with
 * a baseline default, so existing themes keep compiling.
 *
 * ControlKit.kt has input helpers (`Modifier.sliderInput`, `Modifier.holdInput`, …) that already satisfy the input and
 * semantics rules; a theme may use them and only draw, or build its own (the Material 3 theme uses Material's).
 */
interface Controls {
    @Composable fun Screen(p: ScreenProps)
    @Composable fun Panel(p: PanelProps)
    @Composable fun Button(p: ButtonProps)
    @Composable fun IconButton(p: IconButtonProps)
    @Composable fun Toggle(p: ToggleProps)
    @Composable fun Slider(p: SliderProps)
    @Composable fun Progress(p: ProgressProps)
    @Composable fun ListItem(p: ListItemProps)
    @Composable fun Segmented(p: SegmentedProps)
    @Composable fun TextField(p: TextFieldProps)
    @Composable fun Divider(p: DividerProps)
    @Composable fun Icon(p: IconProps)
    @Composable fun DirectionPad(p: DirectionPadProps)
    @Composable fun HoldButton(p: HoldButtonProps)
    @Composable fun Stat(p: StatProps)
}

/** Every props class carries the caller's modifier. */
@Stable
sealed interface Props {
    val modifier: Modifier
}

/** A tappable icon in a top bar: navigation (Back, Close) or an action. */
@Stable
class BarAction(
    val icon: RoseIcon,
    val contentDescription: String,
    val onClick: () -> Unit,
    val enabled: Boolean = true,
    /** Non-null makes it a toggle (e.g. Loop), checked or not. */
    val checked: Boolean? = null,
)

/**
 * A full screen: background, top bar with [title], optional navigation and actions, optional bottom bar, content.
 *
 * Semantics: [title] is visible text marked as a heading; [navigation] and each of [actions] behave as [IconButton]s.
 * The screen draws edge to edge and handles the system bars itself: the top bar sits below the status bar, and the
 * [content] lambda receives the padding (bars plus system insets) it must apply, e.g. as a list's contentPadding.
 */
@Stable
class ScreenProps(
    val title: String,
    override val modifier: Modifier = Modifier,
    val navigation: BarAction? = null,
    val actions: List<BarAction> = emptyList(),
    val bottomBar: (@Composable () -> Unit)? = null,
    val content: @Composable (PaddingValues) -> Unit,
) : Props

enum class PanelKind {
    /** Stands above the background: a card, a post. */
    Raised,
    /** Same plane as the background, set apart by an outline or tone. */
    Flat,
    /** Recessed: a well, a screen inside a device, a game field. */
    Inset,
}

/**
 * A container: a card, a post, a game field. The theme draws its shape, fill, edge and inner padding and sets
 * [LocalContentColor] for [content]; the content adds no outer padding of its own.
 *
 * Semantics: with [onClick], the whole panel is one clickable element (Role.Button) labelled [onClickLabel].
 */
@Stable
class PanelProps(
    override val modifier: Modifier = Modifier,
    val kind: PanelKind = PanelKind.Raised,
    val onClick: (() -> Unit)? = null,
    val onClickLabel: String? = null,
    val content: @Composable () -> Unit,
) : Props

enum class ButtonKind {
    /** The screen's main action. At most one or two per screen. */
    Primary,
    /** Ordinary actions. */
    Secondary,
    /** Low emphasis: text-like. */
    Quiet,
    /** Destructive: delete, reset. */
    Danger,
}

/**
 * A labelled button. Semantics: Role.Button, text [label], click action calling [onClick] once; disabled (no click)
 * when ![enabled]. [icon] is decorative (the label already says it).
 */
@Stable
class ButtonProps(
    val label: String,
    val onClick: () -> Unit,
    override val modifier: Modifier = Modifier,
    val enabled: Boolean = true,
    val kind: ButtonKind = ButtonKind.Secondary,
    val icon: RoseIcon? = null,
) : Props

/**
 * An icon-only button. Semantics: content description [contentDescription]; Role.Button with a click action when
 * [checked] is null; when [checked] is non-null, a toggle (Role.Checkbox, on/off state) whose click calls [onClick]
 * (the caller flips the state). Disabled when ![enabled]. [prominent] marks the screen's main control, e.g. Play.
 */
@Stable
class IconButtonProps(
    val icon: RoseIcon,
    val contentDescription: String,
    val onClick: () -> Unit,
    override val modifier: Modifier = Modifier,
    val enabled: Boolean = true,
    val checked: Boolean? = null,
    val prominent: Boolean = false,
) : Props

/**
 * An on/off setting with its [label]. Semantics: one element, Role.Switch, toggleable with state [checked], text
 * [label]; a click calls [onCheckedChange] with the new value. Disabled when ![enabled].
 */
@Stable
class ToggleProps(
    val checked: Boolean,
    val onCheckedChange: (Boolean) -> Unit,
    val label: String,
    override val modifier: Modifier = Modifier,
    val enabled: Boolean = true,
) : Props

/**
 * A continuous or stepped value: a volume, a step size, or a player's timeline.
 *
 * Semantics: content description [label]; progress range info (current [value], [valueRange], [steps]); a
 * set-progress action; state description [valueText] when given (e.g. "1:23 of 3:40"); disabled when ![enabled].
 * Input: a tap or a drag anywhere along the track moves the value there, calling [onValueChange] with values inside
 * [valueRange] (snapped when [steps] > 0) and [onValueChangeFinished] once when the gesture ends.
 *
 * [highlight] marks a sub-range on the track (a loop passage); [marks] are positions to show (loop points,
 * chapters). Both are display only.
 */
@Stable
class SliderProps(
    val value: Float,
    val onValueChange: (Float) -> Unit,
    val label: String,
    override val modifier: Modifier = Modifier,
    val valueRange: ClosedFloatingPointRange<Float> = 0f..1f,
    val steps: Int = 0,
    val onValueChangeFinished: (() -> Unit)? = null,
    val enabled: Boolean = true,
    val valueText: String? = null,
    val highlight: ClosedFloatingPointRange<Float>? = null,
    val marks: List<Float> = emptyList(),
) : Props

/**
 * Progress toward something. [progress] in 0..1, or null for indeterminate. Semantics: content description [label];
 * progress range info 0..1 at [progress] (indeterminate when null). Not interactive.
 */
@Stable
class ProgressProps(
    val progress: Float?,
    val label: String,
    override val modifier: Modifier = Modifier,
) : Props

/**
 * One row of a list. Semantics: [headline] and [supporting] are text; with [onClick] the row is one clickable element
 * (Role.Button); [selected] is exposed as selected state. [leading] and [trailing] are the caller's content (an
 * avatar, an [IconButton]); controls inside [trailing] stay separately clickable.
 */
@Stable
class ListItemProps(
    val headline: String,
    override val modifier: Modifier = Modifier,
    val supporting: String? = null,
    val leadingIcon: RoseIcon? = null,
    val leading: (@Composable () -> Unit)? = null,
    val trailing: (@Composable () -> Unit)? = null,
    val onClick: (() -> Unit)? = null,
    val selected: Boolean = false,
) : Props

/**
 * Pick one of a few [options] (tabs, a filter, a speed). Semantics: a selectable group described by [label]; each
 * option is its own element with its text, Role.Tab, selected state, and a click calling [onSelect] with its index.
 */
@Stable
class SegmentedProps(
    val options: List<String>,
    val selectedIndex: Int,
    val onSelect: (Int) -> Unit,
    val label: String,
    override val modifier: Modifier = Modifier,
    val enabled: Boolean = true,
) : Props

/**
 * Text entry. Semantics: an editable text field with text [value], labelled [label] (visible or as its description);
 * typing calls [onValueChange]; the IME action (Done for single line) calls [onSubmit] when given.
 */
@Stable
class TextFieldProps(
    val value: String,
    val onValueChange: (String) -> Unit,
    val label: String,
    override val modifier: Modifier = Modifier,
    val placeholder: String? = null,
    val singleLine: Boolean = true,
    val enabled: Boolean = true,
    val onSubmit: (() -> Unit)? = null,
) : Props

/** A thin separator. Not in the semantics tree. */
@Stable
class DividerProps(override val modifier: Modifier = Modifier) : Props

/**
 * One of Rose's icons, drawn this theme's way (the baseline vectors, or the theme's own for the same meaning).
 * [contentDescription] null means decorative. [tint] Unspecified means [LocalContentColor].
 */
@Stable
class IconProps(
    val icon: RoseIcon,
    val contentDescription: String?,
    override val modifier: Modifier = Modifier,
    val tint: Color = Color.Unspecified,
    val size: Dp = 24.dp,
) : Props

/**
 * A game's directional control. Each of [directions] is its own element with content description
 * [Direction.label]: touching it down calls `onDirection(d, true)`, lifting or cancelling calls `onDirection(d, false)`
 * (so holding moves); an accessibility click calls true then false. Directions held by different fingers are independent.
 */
@Stable
class DirectionPadProps(
    val onDirection: (Direction, Boolean) -> Unit,
    override val modifier: Modifier = Modifier,
    val directions: Set<Direction> = Direction.entries.toSet(),
    val enabled: Boolean = true,
) : Props

/**
 * A press-and-hold button (Jump, Fire, Boost). Semantics: Role.Button, content description [label]. Touching down
 * calls `onPressedChange(true)`, lifting or cancelling `onPressedChange(false)`; an accessibility click calls true then
 * false. Works while another finger holds a [DirectionPad].
 */
@Stable
class HoldButtonProps(
    val label: String,
    val onPressedChange: (Boolean) -> Unit,
    override val modifier: Modifier = Modifier,
    val icon: RoseIcon? = null,
    val enabled: Boolean = true,
) : Props

/**
 * A labelled read-out for a HUD or a summary: score, lives, time, a count. Semantics: [label] and [value] as text;
 * with [progress] (0..1), also progress range info (a health or time bar). Not interactive.
 */
@Stable
class StatProps(
    val label: String,
    val value: String,
    override val modifier: Modifier = Modifier,
    val progress: Float? = null,
) : Props

enum class Direction(val label: String) { Up("Up"), Down("Down"), Left("Left"), Right("Right") }
