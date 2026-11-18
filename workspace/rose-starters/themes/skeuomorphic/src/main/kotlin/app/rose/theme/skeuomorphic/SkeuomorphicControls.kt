package app.rose.theme.skeuomorphic

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.asPaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.BasicText
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
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
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

internal object SkeuomorphicControls : Controls {
    @Composable
    override fun Screen(p: ScreenProps) {
        val colors = RoseTheme.colors
        val horizontal = WindowInsets.safeDrawing.only(WindowInsetsSides.Horizontal)
        Column(p.modifier.fillMaxSize().enamel(colors.background)) {
            Column(Modifier.fillMaxWidth().enamel(colors.surface)) {
                Row(
                    Modifier.fillMaxWidth().statusBarsPadding().windowInsetsPadding(horizontal)
                        .heightIn(min = 64.dp).padding(horizontal = 12.dp, vertical = 6.dp),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    ProvideContentColor(colors.onSurface) {
                        p.navigation?.let { BarKey(it) }
                        Text(p.title, Modifier.weight(1f).semantics { heading() }, role = TextRole.Title)
                        p.actions.forEach { BarKey(it) }
                    }
                }
                Divider(DividerProps())
            }
            val contentInsets = if (p.bottomBar == null) {
                WindowInsets.safeDrawing.only(WindowInsetsSides.Horizontal + WindowInsetsSides.Bottom)
            } else {
                horizontal
            }
            Box(Modifier.weight(1f).fillMaxWidth()) {
                ProvideContentColor(colors.onBackground) { p.content(contentInsets.asPaddingValues()) }
            }
            p.bottomBar?.let { bottomBar ->
                Column(Modifier.fillMaxWidth().enamel(colors.surface)) {
                    Divider(DividerProps())
                    Box(Modifier.fillMaxWidth().navigationBarsPadding().windowInsetsPadding(horizontal)) {
                        ProvideContentColor(colors.onSurface) { bottomBar() }
                    }
                }
            }
        }
    }

    @Composable
    private fun BarKey(action: BarAction) = IconButton(
        IconButtonProps(action.icon, action.contentDescription, action.onClick, enabled = action.enabled, checked = action.checked),
    )

    @Composable
    override fun Panel(p: PanelProps) {
        val colors = RoseTheme.colors
        val shape = RoseTheme.shapes.medium
        val interaction = remember { MutableInteractionSource() }
        val click = p.onClick?.let {
            Modifier.minTouchTarget().clickable(interaction, null, role = Role.Button, onClickLabel = p.onClickLabel, onClick = it)
        } ?: Modifier
        val focused by interaction.collectIsFocusedAsState()
        val pressed by interaction.collectIsPressedAsState()
        val shade = if (pressed) 0.10f else 0f
        val housing = when (p.kind) {
            PanelKind.Raised -> Modifier.keycap(colors.surface, shape, interaction)
            PanelKind.Flat -> Modifier.clip(shape).enamel(lerp(colors.surface, Color.Black, shade)).border(1.dp, colors.outline, shape)
            PanelKind.Inset -> Modifier.recessedWell(lerp(colors.surfaceVariant, Color.Black, shade), colors.outline, shape)
        }
        val fill = if (p.kind == PanelKind.Inset) colors.onSurfaceVariant else colors.onSurface
        Box(
            p.modifier.then(click).then(housing)
                .then(if (focused && p.kind != PanelKind.Raised) Modifier.border(2.dp, fill, shape) else Modifier)
                .padding(RoseTheme.spacing.l),
        ) { ProvideContentColor(fill, p.content) }
    }

    @Composable
    override fun Button(p: ButtonProps) {
        val colors = RoseTheme.colors
        val interaction = remember { MutableInteractionSource() }
        val (fill, ink) = when (p.kind) {
            ButtonKind.Primary -> colors.accent to colors.onAccent
            ButtonKind.Secondary -> colors.surface to colors.onSurface
            ButtonKind.Quiet -> colors.surfaceVariant to colors.onSurfaceVariant
            ButtonKind.Danger -> colors.danger to colors.onDanger
        }
        val shape = RoseTheme.shapes.small
        Row(
            p.modifier.minTouchTarget().clickable(interaction, null, enabled = p.enabled, role = Role.Button, onClick = p.onClick)
                .disabledAppearance(p.enabled).keycap(fill, shape, interaction, shallow = p.kind == ButtonKind.Quiet)
                .padding(horizontal = 16.dp, vertical = 9.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.CenterHorizontally),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            ProvideContentColor(ink) {
                p.icon?.let { Icon(IconProps(it, null, size = 18.dp)) }
                Text(p.label, role = TextRole.Label, textAlign = TextAlign.Center)
            }
        }
    }

    @Composable
    override fun IconButton(p: IconButtonProps) {
        val colors = RoseTheme.colors
        val interaction = remember { MutableInteractionSource() }
        val checked = p.checked
        val active = p.prominent || checked == true
        val fill = if (active) colors.accent else colors.surface
        val ink = if (active) colors.onAccent else colors.onSurface
        val input = if (checked != null) {
            Modifier.toggleable(checked, interaction, null, enabled = p.enabled, role = Role.Checkbox) { p.onClick() }
        } else {
            Modifier.clickable(interaction, null, enabled = p.enabled, role = Role.Button, onClick = p.onClick)
        }
        val shape = if (p.prominent) CircleShape else RoseTheme.shapes.small
        Box(
            p.modifier.size(if (p.prominent) 66.dp else 48.dp).minTouchTarget()
                .semantics { contentDescription = p.contentDescription }.then(input).disabledAppearance(p.enabled)
                .keycap(fill, shape, interaction, latched = checked == true),
            contentAlignment = Alignment.Center,
        ) { Icon(IconProps(p.icon, null, tint = ink, size = if (p.prominent) 30.dp else 22.dp)) }
    }

    @Composable
    override fun Toggle(p: ToggleProps) = SlideSwitch(p)

    @Composable
    override fun Slider(p: SliderProps) = GroovedSlider(p)

    @Composable
    override fun Progress(p: ProgressProps) {
        Box(
            p.modifier.fillMaxWidth().semantics {
                contentDescription = p.label
                progressBarRangeInfo = progressInfo(p.progress)
            },
        ) { Meter(p.progress) }
    }

    @Composable
    override fun ListItem(p: ListItemProps) {
        val colors = RoseTheme.colors
        val shape = RoseTheme.shapes.small
        val interaction = remember { MutableInteractionSource() }
        val click = p.onClick?.let { Modifier.clickable(interaction, null, role = Role.Button, onClick = it) } ?: Modifier
        val fill = if (p.selected) colors.surfaceVariant else colors.surface
        val ink = if (p.selected) colors.onSurfaceVariant else colors.onSurface
        Box(p.modifier.fillMaxWidth().then(click).semantics { selected = p.selected }) {
            Box(Modifier.matchParentSize().keycap(fill, shape, interaction, latched = p.selected, shallow = true))
            Row(
                Modifier.heightIn(min = 58.dp).padding(horizontal = 12.dp, vertical = 7.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                ProvideContentColor(ink) {
                    if (p.selected) {
                        Box(Modifier.width(3.dp).height(20.dp).background(colors.accent, CircleShape))
                    }
                    p.leadingIcon?.let {
                        Box(Modifier.size(36.dp).recessedWell(colors.surfaceVariant, colors.outline, CircleShape), contentAlignment = Alignment.Center) {
                            Icon(IconProps(it, null, tint = colors.onSurfaceVariant, size = 20.dp))
                        }
                    }
                    p.leading?.invoke()
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(p.headline)
                        p.supporting?.let { Text(it, role = TextRole.Caption, color = colors.muted) }
                    }
                    p.trailing?.invoke()
                }
            }
        }
    }

    @Composable
    override fun Segmented(p: SegmentedProps) {
        val colors = RoseTheme.colors
        val shape = RoseTheme.shapes.small
        Row(
            p.modifier.fillMaxWidth().semantics { contentDescription = p.label }.selectableGroup()
                .disabledAppearance(p.enabled).recessedWell(colors.surfaceVariant, colors.outline, shape).padding(vertical = 3.dp),
            horizontalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            p.options.forEachIndexed { index, option ->
                val selected = index == p.selectedIndex
                val interaction = remember { MutableInteractionSource() }
                Box(
                    Modifier.weight(1f).minTouchTarget()
                        .selectable(selected, interaction, null, enabled = p.enabled, role = Role.Tab) { p.onSelect(index) }
                        .padding(horizontal = 2.dp)
                        .keycap(if (selected) colors.accent else colors.surface, shape, interaction, latched = selected)
                        .padding(horizontal = 4.dp, vertical = 8.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(option, role = TextRole.Label, color = if (selected) colors.onAccent else colors.onSurface, textAlign = TextAlign.Center)
                }
            }
        }
    }

    @Composable
    override fun TextField(p: TextFieldProps) {
        val colors = RoseTheme.colors
        val shape = RoseTheme.shapes.small
        val interaction = remember { MutableInteractionSource() }
        val focused by interaction.collectIsFocusedAsState()
        BasicTextField(
            value = p.value,
            onValueChange = p.onValueChange,
            modifier = p.modifier.fillMaxWidth().minTouchTarget().semantics { contentDescription = p.label }.disabledAppearance(p.enabled),
            enabled = p.enabled,
            singleLine = p.singleLine,
            interactionSource = interaction,
            textStyle = RoseTheme.type.body.copy(color = colors.onSurfaceVariant),
            cursorBrush = SolidColor(colors.accent),
            keyboardOptions = KeyboardOptions(imeAction = if (p.singleLine) ImeAction.Done else ImeAction.Default),
            keyboardActions = KeyboardActions(onDone = p.onSubmit?.let { submit -> { submit() } }),
            decorationBox = { field ->
                Column(
                    Modifier.recessedWell(colors.surfaceVariant, colors.outline, shape)
                        .then(if (focused) Modifier.border(2.dp, colors.accent, shape) else Modifier)
                        .padding(horizontal = 12.dp, vertical = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(3.dp),
                ) {
                    Text(p.label, role = TextRole.Caption, color = colors.onSurfaceVariant)
                    Box {
                        if (p.value.isEmpty()) p.placeholder?.let { Text(it, color = colors.muted) }
                        field()
                    }
                }
            },
        )
    }

    @Composable
    override fun Divider(p: DividerProps) {
        val colors = RoseTheme.colors
        Column(p.modifier.fillMaxWidth()) {
            Spacer(Modifier.fillMaxWidth().height(1.dp).background(colors.outline))
            Spacer(Modifier.fillMaxWidth().height(1.dp).background(Color.White.copy(alpha = if (colors.isDark) 0.14f else 0.65f)))
        }
    }

    @Composable
    override fun Icon(p: IconProps) {
        val tint = if (p.tint == Color.Unspecified) LocalContentColor.current else p.tint
        Image(RoseIcons.vector(p.icon), p.contentDescription, p.modifier.size(p.size), colorFilter = ColorFilter.tint(tint))
    }

    @Composable
    override fun DirectionPad(p: DirectionPadProps) {
        if (p.directions.none { it == Direction.Up || it == Direction.Down }) {
            CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Ltr) {
                Row(p.modifier, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Direction.entries.filter { it in p.directions }.forEach { PadKey(it, p) }
                }
            }
        } else {
            Box(p.modifier.size(156.dp)) {
                p.directions.forEach { direction ->
                    val position = when (direction) {
                        Direction.Up -> Alignment.TopCenter
                        Direction.Down -> Alignment.BottomCenter
                        Direction.Left -> AbsoluteAlignment.CenterLeft
                        Direction.Right -> AbsoluteAlignment.CenterRight
                    }
                    Box(Modifier.align(position)) { PadKey(direction, p) }
                }
            }
        }
    }

    @Composable
    private fun PadKey(direction: Direction, p: DirectionPadProps) {
        val colors = RoseTheme.colors
        val icon = when (direction) {
            Direction.Up -> RoseIcon.Up
            Direction.Down -> RoseIcon.Down
            Direction.Left -> RoseIcon.Left
            Direction.Right -> RoseIcon.Right
        }
        var pressed by remember { mutableStateOf(false) }
        Box(
            Modifier.size(52.dp).minTouchTarget().holdInput(direction.label, p.enabled) { down ->
                pressed = down
                p.onDirection(direction, down)
            }.disabledAppearance(p.enabled)
                .keycap(if (pressed) colors.accent else colors.surface, RoseTheme.shapes.small, pressed = pressed),
            contentAlignment = Alignment.Center,
        ) { Icon(IconProps(icon, null, tint = if (pressed) colors.onAccent else colors.onSurface)) }
    }

    @Composable
    override fun HoldButton(p: HoldButtonProps) {
        val colors = RoseTheme.colors
        var pressed by remember { mutableStateOf(false) }
        Column(
            p.modifier.widthIn(min = 74.dp).heightIn(min = 66.dp).minTouchTarget().holdInput(p.label, p.enabled) { down ->
                pressed = down
                p.onPressedChange(down)
            }.disabledAppearance(p.enabled).keycap(colors.accent, RoseTheme.shapes.medium, pressed = pressed)
                .padding(horizontal = 13.dp, vertical = 7.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(2.dp, Alignment.CenterVertically),
        ) {
            ProvideContentColor(colors.onAccent) {
                p.icon?.let { Icon(IconProps(it, null, size = 22.dp)) }
                Text(p.label, role = TextRole.Label, textAlign = TextAlign.Center)
            }
        }
    }

    @Composable
    override fun Stat(p: StatProps) {
        val colors = RoseTheme.colors
        Column(
            p.modifier.semantics(mergeDescendants = true) {
                p.progress?.let { progressBarRangeInfo = progressInfo(it) }
            }.recessedWell(colors.surfaceVariant, colors.outline, RoseTheme.shapes.small).padding(horizontal = 10.dp, vertical = 6.dp),
            verticalArrangement = Arrangement.spacedBy(3.dp),
        ) {
            Text(p.label, role = TextRole.Caption, color = colors.onSurfaceVariant)
            BasicText(p.value, style = RoseTheme.type.label.copy(fontFamily = FontFamily.Monospace, color = colors.onSurfaceVariant))
            p.progress?.let { Meter(it, Modifier.width(72.dp)) }
        }
    }
}

internal fun Modifier.disabledAppearance(enabled: Boolean): Modifier = if (enabled) this else alpha(0.53f)

private fun progressInfo(progress: Float?) = progress?.let { ProgressBarRangeInfo(it.coerceIn(0f, 1f), 0f..1f) }
    ?: ProgressBarRangeInfo.Indeterminate
