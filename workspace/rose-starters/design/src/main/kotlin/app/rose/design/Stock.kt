package app.rose.design

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

// The stock controls, as templates call them. Each forwards to the current theme (Controls.kt documents the contract).
// Templates build every screen from these, Text, Compose foundation layout (Row, Column, Box, LazyColumn, Canvas…)
// and RoseTheme.colors / type / shapes / spacing. Nothing here knows any theme.

@Composable
fun Screen(
    title: String,
    modifier: Modifier = Modifier,
    navigation: BarAction? = null,
    actions: List<BarAction> = emptyList(),
    bottomBar: (@Composable () -> Unit)? = null,
    content: @Composable (PaddingValues) -> Unit,
) = RoseTheme.controls.Screen(ScreenProps(title, modifier, navigation, actions, bottomBar, content))

@Composable
fun Panel(
    modifier: Modifier = Modifier,
    kind: PanelKind = PanelKind.Raised,
    onClick: (() -> Unit)? = null,
    onClickLabel: String? = null,
    content: @Composable () -> Unit,
) = RoseTheme.controls.Panel(PanelProps(modifier, kind, onClick, onClickLabel, content))

@Composable
fun Button(
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    kind: ButtonKind = ButtonKind.Secondary,
    icon: RoseIcon? = null,
) = RoseTheme.controls.Button(ButtonProps(label, onClick, modifier, enabled, kind, icon))

@Composable
fun IconButton(
    icon: RoseIcon,
    contentDescription: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    checked: Boolean? = null,
    prominent: Boolean = false,
) = RoseTheme.controls.IconButton(IconButtonProps(icon, contentDescription, onClick, modifier, enabled, checked, prominent))

@Composable
fun Toggle(
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    label: String,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) = RoseTheme.controls.Toggle(ToggleProps(checked, onCheckedChange, label, modifier, enabled))

@Composable
fun Slider(
    value: Float,
    onValueChange: (Float) -> Unit,
    label: String,
    modifier: Modifier = Modifier,
    valueRange: ClosedFloatingPointRange<Float> = 0f..1f,
    steps: Int = 0,
    onValueChangeFinished: (() -> Unit)? = null,
    enabled: Boolean = true,
    valueText: String? = null,
    highlight: ClosedFloatingPointRange<Float>? = null,
    marks: List<Float> = emptyList(),
) = RoseTheme.controls.Slider(
    SliderProps(value, onValueChange, label, modifier, valueRange, steps, onValueChangeFinished, enabled, valueText, highlight, marks),
)

@Composable
fun Progress(progress: Float?, label: String, modifier: Modifier = Modifier) =
    RoseTheme.controls.Progress(ProgressProps(progress, label, modifier))

@Composable
fun ListItem(
    headline: String,
    modifier: Modifier = Modifier,
    supporting: String? = null,
    leadingIcon: RoseIcon? = null,
    leading: (@Composable () -> Unit)? = null,
    trailing: (@Composable () -> Unit)? = null,
    onClick: (() -> Unit)? = null,
    selected: Boolean = false,
) = RoseTheme.controls.ListItem(ListItemProps(headline, modifier, supporting, leadingIcon, leading, trailing, onClick, selected))

@Composable
fun Segmented(
    options: List<String>,
    selectedIndex: Int,
    onSelect: (Int) -> Unit,
    label: String,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) = RoseTheme.controls.Segmented(SegmentedProps(options, selectedIndex, onSelect, label, modifier, enabled))

@Composable
fun TextField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    modifier: Modifier = Modifier,
    placeholder: String? = null,
    singleLine: Boolean = true,
    enabled: Boolean = true,
    onSubmit: (() -> Unit)? = null,
) = RoseTheme.controls.TextField(TextFieldProps(value, onValueChange, label, modifier, placeholder, singleLine, enabled, onSubmit))

@Composable
fun Divider(modifier: Modifier = Modifier) = RoseTheme.controls.Divider(DividerProps(modifier))

@Composable
fun Icon(
    icon: RoseIcon,
    contentDescription: String?,
    modifier: Modifier = Modifier,
    tint: Color = Color.Unspecified,
    size: Dp = 24.dp,
) = RoseTheme.controls.Icon(IconProps(icon, contentDescription, modifier, tint, size))

@Composable
fun DirectionPad(
    onDirection: (Direction, Boolean) -> Unit,
    modifier: Modifier = Modifier,
    directions: Set<Direction> = Direction.entries.toSet(),
    enabled: Boolean = true,
) = RoseTheme.controls.DirectionPad(DirectionPadProps(onDirection, modifier, directions, enabled))

@Composable
fun HoldButton(
    label: String,
    onPressedChange: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
    icon: RoseIcon? = null,
    enabled: Boolean = true,
) = RoseTheme.controls.HoldButton(HoldButtonProps(label, onPressedChange, modifier, icon, enabled))

@Composable
fun Stat(label: String, value: String, modifier: Modifier = Modifier, progress: Float? = null) =
    RoseTheme.controls.Stat(StatProps(label, value, modifier, progress))

/** Which of the theme's text styles to use. */
enum class TextRole { Display, Title, Body, Label, Caption }

/** The style for [role] in the current theme. */
@Composable
fun TextRole.style(): TextStyle = when (this) {
    TextRole.Display -> RoseTheme.type.display
    TextRole.Title -> RoseTheme.type.title
    TextRole.Body -> RoseTheme.type.body
    TextRole.Label -> RoseTheme.type.label
    TextRole.Caption -> RoseTheme.type.caption
}

/**
 * Text in one of the theme's roles. [color] Unspecified means [LocalContentColor] (what the surrounding panel,
 * button or screen set); pass `RoseTheme.colors.muted` for secondary text.
 */
@Composable
fun Text(
    text: String,
    modifier: Modifier = Modifier,
    role: TextRole = TextRole.Body,
    color: Color = Color.Unspecified,
    textAlign: TextAlign? = null,
    maxLines: Int = Int.MAX_VALUE,
    overflow: TextOverflow = TextOverflow.Clip,
) {
    val style = role.style()
    val resolved = when {
        color != Color.Unspecified -> color
        style.color != Color.Unspecified -> style.color
        else -> LocalContentColor.current
    }
    BasicText(
        text = text,
        modifier = modifier,
        style = style.merge(TextStyle(color = resolved, textAlign = textAlign ?: TextAlign.Unspecified)),
        maxLines = maxLines,
        overflow = overflow,
    )
}
