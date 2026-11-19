package app.rose.theme.minimal

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.interaction.collectIsHoveredAsState
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.asPaddingValues
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.AbsoluteAlignment
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import app.rose.design.BarAction
import app.rose.design.ButtonKind
import app.rose.design.ButtonProps
import app.rose.design.Controls
import app.rose.design.Direction
import app.rose.design.DirectionPadProps
import app.rose.design.DividerProps
import app.rose.design.HoldButtonProps
import app.rose.design.IconButtonProps
import app.rose.design.IconProps
import app.rose.design.ListItemProps
import app.rose.design.LocalContentColor
import app.rose.design.PanelKind
import app.rose.design.PanelProps
import app.rose.design.ProgressProps
import app.rose.design.ProvideContentColor
import app.rose.design.RoseIcon
import app.rose.design.RoseIcons
import app.rose.design.RoseTheme
import app.rose.design.ScreenProps
import app.rose.design.SegmentedProps
import app.rose.design.SliderProps
import app.rose.design.StatProps
import app.rose.design.Text
import app.rose.design.TextFieldProps
import app.rose.design.TextRole
import app.rose.design.ToggleProps
import app.rose.design.holdInput
import app.rose.design.minTouchTarget

internal object MinimalControls : Controls {
    @Composable
    override fun Screen(p: ScreenProps) {
        val c = RoseTheme.colors
        val s = RoseTheme.spacing
        val safeDrawing = WindowInsets.safeDrawing
        val horizontal = WindowInsetsSides.Horizontal
        Column(p.modifier.fillMaxSize().background(c.background)) {
            Column(
                Modifier.fillMaxWidth().windowInsetsPadding(safeDrawing.only(horizontal + WindowInsetsSides.Top)),
            ) {
                ProvideContentColor(c.onBackground) {
                    if (p.navigation != null || p.actions.isNotEmpty()) {
                        Row(
                            Modifier.fillMaxWidth().padding(horizontal = s.s),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            p.navigation?.let { BarIcon(it) }
                            Spacer(Modifier.weight(1f))
                            Row(Modifier.horizontalScroll(rememberScrollState())) {
                                p.actions.forEach { BarIcon(it) }
                            }
                        }
                    }
                    Text(
                        p.title,
                        Modifier.padding(start = s.l, end = s.l, top = s.m, bottom = s.l).semantics { heading() },
                        role = TextRole.Display,
                    )
                    Box(Modifier.padding(horizontal = s.l)) { Divider(DividerProps(Modifier)) }
                }
            }
            val contentSides = if (p.bottomBar == null) horizontal + WindowInsetsSides.Bottom else horizontal
            Box(Modifier.weight(1f).fillMaxWidth()) {
                ProvideContentColor(c.onBackground) { p.content(safeDrawing.only(contentSides).asPaddingValues()) }
            }
            p.bottomBar?.let { bar ->
                Column(
                    Modifier.fillMaxWidth().background(c.surface)
                        .windowInsetsPadding(safeDrawing.only(horizontal + WindowInsetsSides.Bottom)),
                ) {
                    Divider(DividerProps(Modifier))
                    ProvideContentColor(c.onSurface) { bar() }
                }
            }
        }
    }

    @Composable
    private fun BarIcon(action: BarAction) {
        IconButton(
            IconButtonProps(
                action.icon, action.contentDescription, action.onClick,
                enabled = action.enabled, checked = action.checked,
            ),
        )
    }

    @Composable
    override fun Panel(p: PanelProps) {
        val c = RoseTheme.colors
        val s = RoseTheme.spacing
        val shape = RoseTheme.shapes.medium
        val onClick = p.onClick
        val fill = when (p.kind) {
            PanelKind.Raised -> c.surface
            PanelKind.Flat -> c.background
            PanelKind.Inset -> c.surfaceVariant
        }
        val ink = when (p.kind) {
            PanelKind.Raised -> c.onSurface
            PanelKind.Flat -> c.onBackground
            PanelKind.Inset -> c.onSurfaceVariant
        }
        var frame = p.modifier.clip(shape).background(fill)
        if (onClick != null) {
            val interaction = remember { MutableInteractionSource() }
            frame = frame.minTouchTarget()
                .clickable(interaction, indication = null, onClickLabel = p.onClickLabel, role = Role.Button, onClick = onClick)
                .interactionFrame(interaction, ink, shape, c.outline)
        } else {
            frame = frame.border(1.dp, c.outline, shape)
        }
        Box(frame.padding(if (p.kind == PanelKind.Inset) s.m else s.l)) {
            ProvideContentColor(ink, p.content)
        }
    }

    @Composable
    override fun Button(p: ButtonProps) {
        val c = RoseTheme.colors
        val shape = RoseTheme.shapes.small
        val s = RoseTheme.spacing
        val interaction = remember { MutableInteractionSource() }
        val disabledPrimary = !p.enabled && p.kind == ButtonKind.Primary
        val fill = when {
            disabledPrimary -> c.surfaceVariant
            p.kind == ButtonKind.Primary -> c.onBackground
            else -> Color.Transparent
        }
        val ink = when {
            !p.enabled -> c.muted
            p.kind == ButtonKind.Primary -> c.background
            p.kind == ButtonKind.Danger -> c.danger
            else -> LocalContentColor.current
        }
        val edge = if (p.kind == ButtonKind.Secondary || disabledPrimary) c.outline else Color.Transparent
        Row(
            p.modifier.minTouchTarget().clip(shape).background(fill)
                .clickable(interaction, indication = null, enabled = p.enabled, role = Role.Button, onClick = p.onClick)
                .interactionFrame(interaction, ink, shape, edge, focusColor = if (p.kind == ButtonKind.Primary) ink else c.accent)
                .padding(horizontal = s.l, vertical = s.s),
            horizontalArrangement = Arrangement.spacedBy(s.s, Alignment.CenterHorizontally),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            ProvideContentColor(ink) {
                p.icon?.let { Icon(IconProps(it, null, size = 18.dp)) }
                Text(p.label, role = TextRole.Label)
            }
        }
    }

    @Composable
    override fun IconButton(p: IconButtonProps) {
        val c = RoseTheme.colors
        val shape = RoseTheme.shapes.small
        val interaction = remember { MutableInteractionSource() }
        val checked = p.checked == true
        val variantFill = checked || (!p.enabled && p.prominent)
        val fill = when {
            variantFill -> c.surfaceVariant
            p.prominent -> c.onBackground
            else -> Color.Transparent
        }
        val ink = when {
            !p.enabled -> c.muted
            checked -> c.accent
            p.prominent -> c.background
            else -> LocalContentColor.current
        }
        val edge = if (variantFill) c.outline else Color.Transparent
        val click = p.checked?.let {
            Modifier.toggleable(it, interaction, indication = null, enabled = p.enabled, role = Role.Checkbox) { p.onClick() }
        } ?: Modifier.clickable(interaction, indication = null, enabled = p.enabled, role = Role.Button, onClick = p.onClick)
        Box(
            p.modifier.size(if (p.prominent) 60.dp else RoseTheme.tokens.minTouchTarget)
                .clip(shape).background(fill).then(click)
                .semantics { contentDescription = p.contentDescription }
                .interactionFrame(interaction, ink, shape, edge, focusColor = if (p.prominent && !checked) ink else c.accent),
            contentAlignment = Alignment.Center,
        ) {
            Icon(IconProps(p.icon, null, tint = ink, size = if (p.prominent) 28.dp else 22.dp))
            if (checked) {
                Spacer(Modifier.align(Alignment.BottomCenter).padding(bottom = 4.dp).size(16.dp, 2.dp).background(ink))
            }
        }
    }

    @Composable
    override fun Toggle(p: ToggleProps) {
        val c = RoseTheme.colors
        val s = RoseTheme.spacing
        val shape = RoseTheme.shapes.small
        val interaction = remember { MutableInteractionSource() }
        val labelColor = if (p.enabled) LocalContentColor.current else c.muted
        Row(
            p.modifier.fillMaxWidth().minTouchTarget().clip(shape)
                .toggleable(p.checked, interaction, indication = null, enabled = p.enabled, role = Role.Switch, onValueChange = p.onCheckedChange)
                .interactionFrame(interaction, labelColor, shape).padding(horizontal = s.s, vertical = s.s),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(s.m),
        ) {
            Text(p.label, Modifier.weight(1f), color = labelColor)
            val line = when {
                !p.enabled -> c.muted
                p.checked -> c.accent
                else -> c.outline
            }
            Box(
                Modifier.size(38.dp, 22.dp).clip(shape)
                    .background(if (p.checked && p.enabled) c.accent else c.surfaceVariant)
                    .border(1.dp, line, shape).padding(horizontal = 4.dp),
                contentAlignment = if (p.checked) Alignment.CenterEnd else Alignment.CenterStart,
            ) {
                Spacer(Modifier.size(12.dp, 12.dp).background(if (p.checked && p.enabled) c.onAccent else line))
            }
        }
    }

    @Composable
    override fun Slider(p: SliderProps) = MinimalSlider(p)

    @Composable
    override fun Progress(p: ProgressProps) = MinimalProgress(p)

    @Composable
    override fun ListItem(p: ListItemProps) {
        val c = RoseTheme.colors
        val s = RoseTheme.spacing
        val interaction = remember { MutableInteractionSource() }
        val click = p.onClick?.let {
            Modifier.clickable(interaction, indication = null, role = Role.Button, onClick = it)
        } ?: Modifier
        val ink = if (p.selected) c.onSurfaceVariant else LocalContentColor.current
        val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
        Column(
            p.modifier.fillMaxWidth().minTouchTarget().then(click).semantics { selected = p.selected }
                .background(if (p.selected) c.surfaceVariant else Color.Transparent)
                .drawBehind {
                    if (p.selected) {
                        val rule = 3.dp.toPx()
                        drawRect(c.accent, Offset(if (rtl) size.width - rule else 0f, 0f), Size(rule, size.height))
                    }
                }
                .interactionFrame(interaction, ink, RectangleShape),
        ) {
            Row(
                Modifier.fillMaxWidth().heightIn(min = RoseTheme.tokens.minTouchTarget)
                    .padding(start = s.l, end = s.s, top = s.m, bottom = s.m),
                horizontalArrangement = Arrangement.spacedBy(s.m),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                ProvideContentColor(ink) {
                    p.leadingIcon?.let { Icon(IconProps(it, null, size = 20.dp)) }
                    p.leading?.let { leading -> Box { leading() } }
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(s.xs)) {
                        Text(p.headline, role = if (p.selected) TextRole.Label else TextRole.Body)
                        p.supporting?.let { Text(it, role = TextRole.Caption, color = c.muted) }
                    }
                    p.trailing?.let { trailing -> Box { trailing() } }
                }
            }
            Divider(DividerProps(Modifier))
        }
    }

    @Composable
    override fun Segmented(p: SegmentedProps) {
        val c = RoseTheme.colors
        val s = RoseTheme.spacing
        val target = RoseTheme.tokens.minTouchTarget
        BoxWithConstraints(
            p.modifier.fillMaxWidth().semantics { contentDescription = p.label }.selectableGroup(),
            propagateMinConstraints = true,
        ) {
            val minWidth = target * p.options.size
            val stripWidth = if (constraints.hasBoundedWidth) maxOf(minWidth, maxWidth) else minWidth
            Row(Modifier.horizontalScroll(rememberScrollState()).width(stripWidth).height(IntrinsicSize.Min)) {
                p.options.forEachIndexed { index, label ->
                    val selected = index == p.selectedIndex
                    val interaction = remember { MutableInteractionSource() }
                    val ink = when {
                        !p.enabled -> c.muted
                        selected -> c.accent
                        else -> LocalContentColor.current
                    }
                    Box(
                        Modifier.weight(1f).fillMaxHeight().minTouchTarget()
                            .selectable(selected, interaction, indication = null, enabled = p.enabled, role = Role.Tab) {
                                p.onSelect(index)
                            }
                            .interactionFrame(interaction, ink, RectangleShape),
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(
                            label, Modifier.padding(horizontal = s.xs, vertical = s.m),
                            role = if (selected) TextRole.Label else TextRole.Body, color = ink,
                        )
                        Spacer(
                            Modifier.align(Alignment.BottomCenter).fillMaxWidth()
                                .height(if (selected) 3.dp else 1.dp)
                                .background(if (selected) ink else c.outline.copy(alpha = 0.55f)),
                        )
                    }
                }
            }
        }
    }

    @Composable
    override fun TextField(p: TextFieldProps) {
        val c = RoseTheme.colors
        val s = RoseTheme.spacing
        val interaction = remember { MutableInteractionSource() }
        val focused by interaction.collectIsFocusedAsState()
        val ink = if (p.enabled) c.onSurface else c.muted
        BasicTextField(
            value = p.value,
            onValueChange = p.onValueChange,
            modifier = p.modifier.fillMaxWidth().minTouchTarget().semantics { contentDescription = p.label },
            enabled = p.enabled,
            singleLine = p.singleLine,
            interactionSource = interaction,
            textStyle = RoseTheme.type.body.copy(color = ink),
            cursorBrush = SolidColor(c.accent),
            keyboardOptions = KeyboardOptions(imeAction = if (p.singleLine) ImeAction.Done else ImeAction.Default),
            keyboardActions = KeyboardActions(onDone = { p.onSubmit?.invoke() }),
            decorationBox = { inner ->
                Column(Modifier.fillMaxWidth().background(if (p.enabled) c.surface else c.surfaceVariant)) {
                    Column(
                        Modifier.fillMaxWidth().padding(horizontal = s.m, vertical = s.s),
                        verticalArrangement = Arrangement.spacedBy(s.xs),
                    ) {
                        Text(p.label, role = TextRole.Caption, color = if (focused) c.accent else c.muted)
                        Box {
                            if (p.value.isEmpty()) p.placeholder?.let { Text(it, color = c.muted) }
                            inner()
                        }
                    }
                    Spacer(
                        Modifier.fillMaxWidth().height(2.dp)
                            .background(if (focused) c.accent else c.outline),
                    )
                }
            },
        )
    }

    @Composable
    override fun Divider(p: DividerProps) {
        Spacer(p.modifier.fillMaxWidth().height(1.dp).background(RoseTheme.colors.outline.copy(alpha = 0.45f)))
    }

    @Composable
    override fun Icon(p: IconProps) {
        val tint = if (p.tint == Color.Unspecified) LocalContentColor.current else p.tint
        Image(
            imageVector = RoseIcons.vector(p.icon),
            contentDescription = p.contentDescription,
            modifier = p.modifier.size(p.size),
            colorFilter = ColorFilter.tint(tint),
        )
    }

    @Composable
    override fun DirectionPad(p: DirectionPadProps) {
        val target = RoseTheme.tokens.minTouchTarget
        val gap = RoseTheme.spacing.xs
        val horizontalOnly = Direction.Up !in p.directions && Direction.Down !in p.directions
        if (horizontalOnly) {
            CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Ltr) {
                Row(p.modifier, horizontalArrangement = Arrangement.spacedBy(gap)) {
                    Direction.entries.filter { it in p.directions }.forEach { PadKey(it, p, Modifier) }
                }
            }
        } else {
            Box(p.modifier.size(target * 3 + gap * 2)) {
                Direction.entries.filter { it in p.directions }.forEach { direction ->
                    val alignment = when (direction) {
                        Direction.Up -> Alignment.TopCenter
                        Direction.Down -> Alignment.BottomCenter
                        Direction.Left -> AbsoluteAlignment.CenterLeft
                        Direction.Right -> AbsoluteAlignment.CenterRight
                    }
                    PadKey(direction, p, Modifier.align(alignment))
                }
            }
        }
    }

    @Composable
    private fun PadKey(direction: Direction, p: DirectionPadProps, modifier: Modifier) {
        val icon = when (direction) {
            Direction.Up -> RoseIcon.Up
            Direction.Down -> RoseIcon.Down
            Direction.Left -> RoseIcon.Left
            Direction.Right -> RoseIcon.Right
        }
        HoldSurface(modifier, direction.label, p.enabled, { p.onDirection(direction, it) }) {
            Icon(IconProps(icon, null, size = 22.dp))
        }
    }

    @Composable
    override fun HoldButton(p: HoldButtonProps) {
        val s = RoseTheme.spacing
        HoldSurface(p.modifier, p.label, p.enabled, p.onPressedChange) {
            Row(
                Modifier.padding(horizontal = s.m, vertical = s.s),
                horizontalArrangement = Arrangement.spacedBy(s.s),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                p.icon?.let { Icon(IconProps(it, null, size = 18.dp)) }
                Text(p.label, role = TextRole.Label)
            }
        }
    }

    @Composable
    private fun HoldSurface(
        modifier: Modifier,
        label: String,
        enabled: Boolean,
        onPressedChange: (Boolean) -> Unit,
        content: @Composable () -> Unit,
    ) {
        val c = RoseTheme.colors
        val shape = RoseTheme.shapes.small
        var held by remember { mutableStateOf(false) }
        val active = enabled && held
        val fill = if (active) c.onBackground else c.surface
        val ink = when {
            !enabled -> c.muted
            active -> c.background
            else -> c.onSurface
        }
        Box(
            modifier.minTouchTarget().clip(shape).background(fill).border(1.dp, if (active) fill else c.outline, shape)
                .holdInput(label, enabled) {
                    held = it
                    onPressedChange(it)
                },
            contentAlignment = Alignment.Center,
        ) { ProvideContentColor(ink, content) }
    }

    @Composable
    override fun Stat(p: StatProps) {
        val c = RoseTheme.colors
        val fraction = p.progress?.coerceIn(0f, 1f)
        Column(
            p.modifier.semantics(mergeDescendants = true) {
                fraction?.let { progressBarRangeInfo = ProgressBarRangeInfo(it, 0f..1f) }
            },
            verticalArrangement = Arrangement.spacedBy(RoseTheme.spacing.xs),
        ) {
            Text(p.label, role = TextRole.Caption, color = c.muted)
            Text(p.value, role = TextRole.Title)
            fraction?.let { MinimalProgressBar(it, Modifier.width(80.dp)) }
        }
    }
}

/** Quiet press and hover fills keep the label fixed; keyboard focus gets a visible outline. */
@Composable
private fun Modifier.interactionFrame(
    source: MutableInteractionSource,
    ink: Color,
    shape: Shape,
    edge: Color = Color.Transparent,
    focusColor: Color = RoseTheme.colors.accent,
): Modifier {
    val pressed by source.collectIsPressedAsState()
    val hovered by source.collectIsHoveredAsState()
    val focused by source.collectIsFocusedAsState()
    val fill = when {
        pressed -> ink.copy(alpha = 0.14f)
        hovered -> ink.copy(alpha = 0.07f)
        else -> Color.Transparent
    }
    return background(fill).border(if (focused) 2.dp else 1.dp, if (focused) focusColor else edge, shape)
}
