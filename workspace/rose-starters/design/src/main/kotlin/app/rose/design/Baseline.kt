package app.rose.design

import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
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
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.selection.toggleable
import androidx.compose.ui.draw.shadow
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Rose's plain default look, drawn with Compose foundation only. It is the fallback, the reference implementation
 * theme authors can read (every control meets the Controls contract with ControlKit's helpers), and one of the two
 * themes in the runnable sample. It is not one of the pilot themes.
 */
object BaselineTheme : Theme {
    override val id = "baseline"
    override val displayName = "Baseline"
    override val controls: Controls = BaselineControls

    private val light = RoseColors(
        background = Color(0xFFF6F6F8), onBackground = Color(0xFF1B1B1F),
        surface = Color(0xFFFFFFFF), onSurface = Color(0xFF1B1B1F), muted = Color(0xFF5D5E66),
        surfaceVariant = Color(0xFFE9E9EE), onSurfaceVariant = Color(0xFF2E2F35),
        accent = Color(0xFFAD2752), onAccent = Color(0xFFFFFFFF), outline = Color(0xFF8A8B94),
        danger = Color(0xFFB3261E), onDanger = Color(0xFFFFFFFF), isDark = false,
    )
    private val dark = RoseColors(
        background = Color(0xFF121214), onBackground = Color(0xFFEDEDF1),
        surface = Color(0xFF1D1D21), onSurface = Color(0xFFEDEDF1), muted = Color(0xFFA9AAB3),
        surfaceVariant = Color(0xFF2C2C32), onSurfaceVariant = Color(0xFFE1E1E7),
        accent = Color(0xFFFF8DAA), onAccent = Color(0xFF3B0717), outline = Color(0xFF7D7E87),
        danger = Color(0xFFFFB4AB), onDanger = Color(0xFF690005), isDark = true,
    )
    private val type = RoseType(
        display = TextStyle(fontFamily = FontFamily.Default, fontWeight = FontWeight.Bold, fontSize = 32.sp, lineHeight = 40.sp),
        title = TextStyle(fontFamily = FontFamily.Default, fontWeight = FontWeight.SemiBold, fontSize = 20.sp, lineHeight = 26.sp),
        body = TextStyle(fontFamily = FontFamily.Default, fontSize = 16.sp, lineHeight = 22.sp),
        label = TextStyle(fontFamily = FontFamily.Default, fontWeight = FontWeight.Medium, fontSize = 14.sp, lineHeight = 20.sp),
        caption = TextStyle(fontFamily = FontFamily.Default, fontSize = 12.sp, lineHeight = 16.sp),
    )
    private val shapes = RoseShapes(small = RoundedCornerShape(10.dp), medium = RoundedCornerShape(14.dp), large = RoundedCornerShape(22.dp))

    override fun tokens(dark: Boolean) = RoseTokens(colors = if (dark) this.dark else light, type = type, shapes = shapes)
}

/** The baseline controls. Public so a theme can delegate a control it has not drawn yet (placeholders do). */
object BaselineControls : Controls {

    @Composable
    override fun Screen(p: ScreenProps) {
        val c = RoseTheme.colors
        Column(p.modifier.fillMaxSize().background(c.background)) {
            Row(
                Modifier.fillMaxWidth().background(c.surface).statusBarsPadding()
                    .windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Horizontal))
                    .heightIn(min = 56.dp).padding(horizontal = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                ProvideContentColor(c.onSurface) {
                    p.navigation?.let { BarIcon(it) } ?: Spacer(Modifier.width(12.dp))
                    Text(
                        p.title, Modifier.weight(1f).semantics { heading() }, role = TextRole.Title,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                    p.actions.forEach { BarIcon(it) }
                }
            }
            Box(Modifier.fillMaxWidth().height(1.dp).background(c.outline.copy(alpha = 0.35f)))
            val sides = if (p.bottomBar == null) WindowInsetsSides.Horizontal + WindowInsetsSides.Bottom else WindowInsetsSides.Horizontal
            val padding = WindowInsets.safeDrawing.only(sides).asPaddingValues()
            Box(Modifier.weight(1f).fillMaxWidth()) {
                ProvideContentColor(c.onBackground) { p.content(padding) }
            }
            p.bottomBar?.let { bar ->
                Box(
                    Modifier.fillMaxWidth().background(c.surface).navigationBarsPadding()
                        .windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Horizontal)),
                ) { ProvideContentColor(c.onSurface) { bar() } }
            }
        }
    }

    @Composable
    private fun BarIcon(a: BarAction) =
        IconButton(IconButtonProps(a.icon, a.contentDescription, a.onClick, enabled = a.enabled, checked = a.checked))

    @Composable
    override fun Panel(p: PanelProps) {
        val c = RoseTheme.colors
        val shape = RoseTheme.shapes.medium
        val (fill, content) = when (p.kind) {
            PanelKind.Raised, PanelKind.Flat -> c.surface to c.onSurface
            PanelKind.Inset -> c.surfaceVariant to c.onSurfaceVariant
        }
        var m = p.modifier
        if (p.kind == PanelKind.Raised) m = m.shadow(2.dp, shape)
        m = m.clip(shape).background(fill)
        if (p.kind == PanelKind.Flat) m = m.border(1.dp, c.outline.copy(alpha = 0.6f), shape)
        p.onClick?.let { m = m.clickable(onClickLabel = p.onClickLabel, role = Role.Button, onClick = it) }
        Box(m.padding(RoseTheme.spacing.l)) { ProvideContentColor(content) { p.content() } }
    }

    @Composable
    override fun Button(p: ButtonProps) {
        val c = RoseTheme.colors
        val interaction = remember { MutableInteractionSource() }
        val pressed by interaction.collectIsPressedAsState()
        val (fill, content, edge) = when (p.kind) {
            ButtonKind.Primary -> Triple(c.accent, c.onAccent, null)
            // Outlined, so it stays visible on any surface, an Inset panel's surfaceVariant included.
            ButtonKind.Secondary -> Triple(c.surface, c.onSurface, c.outline)
            ButtonKind.Quiet -> Triple(Color.Transparent, c.accent, null)
            ButtonKind.Danger -> Triple(Color.Transparent, c.danger, c.danger)
        }
        val shape = RoseTheme.shapes.small
        var m = p.modifier.minTouchTarget().alpha(if (p.enabled) 1f else 0.38f).clip(shape).background(fill)
        if (edge != null) m = m.border(1.dp, edge, shape)
        m = m.clickable(interaction, indication = null, enabled = p.enabled, role = Role.Button, onClick = p.onClick)
        Row(
            m.background(if (pressed) content.copy(alpha = 0.12f) else Color.Transparent).padding(horizontal = 20.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            ProvideContentColor(content) {
                p.icon?.let { Icon(IconProps(it, null, Modifier.padding(end = 8.dp), size = 18.dp)) }
                Text(p.label, role = TextRole.Label)
            }
        }
    }

    @Composable
    override fun IconButton(p: IconButtonProps) {
        val c = RoseTheme.colors
        val interaction = remember { MutableInteractionSource() }
        val pressed by interaction.collectIsPressedAsState()
        val on = p.checked == true
        val fill = when {
            p.prominent -> c.accent
            on -> c.accent.copy(alpha = 0.16f)
            else -> Color.Transparent
        }
        val tint = when {
            p.prominent -> c.onAccent
            on -> c.accent
            else -> LocalContentColor.current
        }
        var m = p.modifier.size(if (p.prominent) 64.dp else 48.dp).alpha(if (p.enabled) 1f else 0.38f).clip(CircleShape).background(fill)
        m = if (p.checked != null) {
            m.toggleable(p.checked, interaction, indication = null, enabled = p.enabled, role = Role.Checkbox) { p.onClick() }
        } else {
            m.clickable(interaction, indication = null, enabled = p.enabled, role = Role.Button, onClick = p.onClick)
        }
        Box(
            m.semantics { contentDescription = p.contentDescription }
                .background(if (pressed) tint.copy(alpha = 0.14f) else Color.Transparent),
            contentAlignment = Alignment.Center,
        ) { Icon(IconProps(p.icon, null, tint = tint, size = if (p.prominent) 30.dp else 24.dp)) }
    }

    @Composable
    override fun Toggle(p: ToggleProps) {
        val c = RoseTheme.colors
        val knob by animateDpAsState(if (p.checked) 22.dp else 2.dp, label = "toggle")
        Row(
            p.modifier.fillMaxWidth().toggleable(p.checked, enabled = p.enabled, role = Role.Switch, onValueChange = p.onCheckedChange)
                .heightIn(min = 48.dp).alpha(if (p.enabled) 1f else 0.38f),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(p.label, Modifier.weight(1f).padding(end = 12.dp))
            Box(
                Modifier.size(width = 50.dp, height = 30.dp).clip(CircleShape)
                    .background(if (p.checked) c.accent else c.surfaceVariant)
                    .border(1.dp, if (p.checked) c.accent else c.outline, CircleShape),
            ) {
                Box(
                    Modifier.offset(x = knob, y = 2.dp).size(26.dp).clip(CircleShape)
                        .background(if (p.checked) c.onAccent else c.outline),
                )
            }
        }
    }

    @Composable
    override fun Slider(p: SliderProps) {
        val c = RoseTheme.colors
        val fraction = sliderFraction(p.value, p.valueRange)
        val highlight = p.highlight?.let { sliderFraction(it.start, p.valueRange)..sliderFraction(it.endInclusive, p.valueRange) }
        val marks = p.marks.map { sliderFraction(it, p.valueRange) }
        Box(p.modifier.fillMaxWidth().heightIn(min = 48.dp).sliderInput(p, thumbInset = 12.dp).alpha(if (p.enabled) 1f else 0.38f)) {
            Canvas(Modifier.fillMaxWidth().height(48.dp).align(Alignment.Center)) {
                val inset = 12.dp.toPx()
                val w = size.width - 2 * inset
                val cy = size.height / 2
                val track = 4.dp.toPx()
                fun x(f: Float) = inset + f * w
                drawRoundRect(c.surfaceVariant, Offset(inset, cy - track / 2), Size(w, track), CornerRadius(track))
                highlight?.let {
                    val band = 12.dp.toPx()
                    drawRoundRect(c.accent.copy(alpha = 0.25f), Offset(x(it.start), cy - band / 2), Size(x(it.endInclusive) - x(it.start), band), CornerRadius(band / 2))
                }
                drawRoundRect(c.accent, Offset(inset, cy - track / 2), Size(fraction * w, track), CornerRadius(track))
                marks.forEach { drawRect(c.onBackground, Offset(x(it) - 1.dp.toPx(), cy - 8.dp.toPx()), Size(2.dp.toPx(), 16.dp.toPx())) }
                drawCircle(c.surface, 11.dp.toPx(), Offset(x(fraction), cy))
                drawCircle(c.accent, 9.dp.toPx(), Offset(x(fraction), cy))
            }
        }
    }

    @Composable
    override fun Progress(p: ProgressProps) {
        val c = RoseTheme.colors
        Box(
            p.modifier.fillMaxWidth().height(6.dp).clip(CircleShape).background(c.surfaceVariant)
                .semantics {
                    contentDescription = p.label
                    progressBarRangeInfo = p.progress?.let { ProgressBarRangeInfo(it.coerceIn(0f, 1f), 0f..1f) } ?: ProgressBarRangeInfo.Indeterminate
                },
        ) {
            if (p.progress != null) {
                Box(Modifier.fillMaxWidth(p.progress.coerceIn(0f, 1f)).fillMaxHeight().background(c.accent))
            } else {
                val t by rememberInfiniteTransition(label = "progress").animateFloat(0f, 1f, infiniteRepeatable(tween(1100)), label = "sweep")
                Canvas(Modifier.fillMaxSize()) { drawRect(c.accent, Offset(size.width * (t * 1.3f - 0.3f), 0f), Size(size.width * 0.3f, size.height)) }
            }
        }
    }

    @Composable
    override fun ListItem(p: ListItemProps) {
        val c = RoseTheme.colors
        var m = p.modifier.fillMaxWidth()
        p.onClick?.let { m = m.clickable(role = Role.Button, onClick = it) }
        Row(
            m.semantics { selected = p.selected }.background(if (p.selected) c.accent.copy(alpha = 0.10f) else Color.Transparent)
                .heightIn(min = 56.dp).padding(horizontal = 16.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            p.leadingIcon?.let { Icon(IconProps(it, null, Modifier.padding(end = 16.dp))) }
            p.leading?.let { Box(Modifier.padding(end = 16.dp)) { it() } }
            Column(Modifier.weight(1f)) {
                Text(p.headline, maxLines = 2, overflow = TextOverflow.Ellipsis)
                p.supporting?.let { Text(it, role = TextRole.Caption, color = c.muted, maxLines = 2, overflow = TextOverflow.Ellipsis) }
            }
            p.trailing?.let { Box(Modifier.padding(start = 8.dp)) { it() } }
        }
    }

    @Composable
    override fun Segmented(p: SegmentedProps) {
        val c = RoseTheme.colors
        val shape = RoseTheme.shapes.small
        Row(
            p.modifier.fillMaxWidth().semantics { contentDescription = p.label }.selectableGroup()
                .clip(shape).border(1.dp, c.outline, shape).alpha(if (p.enabled) 1f else 0.38f),
        ) {
            p.options.forEachIndexed { i, option ->
                val on = i == p.selectedIndex
                Box(
                    Modifier.weight(1f).heightIn(min = 48.dp).background(if (on) c.accent else Color.Transparent)
                        .selectable(on, enabled = p.enabled, role = Role.Tab) { p.onSelect(i) },
                    contentAlignment = Alignment.Center,
                ) { Text(option, role = TextRole.Label, color = if (on) c.onAccent else c.onBackground, maxLines = 1) }
            }
        }
    }

    @Composable
    override fun TextField(p: TextFieldProps) {
        val c = RoseTheme.colors
        BasicTextField(
            value = p.value,
            onValueChange = p.onValueChange,
            modifier = p.modifier.fillMaxWidth().semantics { contentDescription = p.label },
            enabled = p.enabled,
            singleLine = p.singleLine,
            textStyle = RoseTheme.type.body.copy(color = c.onSurface),
            cursorBrush = SolidColor(c.accent),
            keyboardOptions = KeyboardOptions(imeAction = if (p.singleLine) ImeAction.Done else ImeAction.Default),
            keyboardActions = KeyboardActions(onDone = { p.onSubmit?.invoke() }),
            decorationBox = { inner ->
                Column(
                    Modifier.clip(RoseTheme.shapes.small).background(c.surface).border(1.dp, c.outline, RoseTheme.shapes.small)
                        .heightIn(min = 56.dp).padding(horizontal = 14.dp, vertical = 8.dp),
                ) {
                    Text(p.label, role = TextRole.Caption, color = c.muted)
                    Box {
                        if (p.value.isEmpty() && p.placeholder != null) Text(p.placeholder, color = c.muted)
                        inner()
                    }
                }
            },
        )
    }

    @Composable
    override fun Divider(p: DividerProps) {
        Box(p.modifier.fillMaxWidth().height(1.dp).background(RoseTheme.colors.outline.copy(alpha = 0.4f)))
    }

    @Composable
    override fun Icon(p: IconProps) {
        val tint = if (p.tint != Color.Unspecified) p.tint else LocalContentColor.current
        Image(
            imageVector = RoseIcons.vector(p.icon),
            contentDescription = p.contentDescription,
            modifier = p.modifier.size(p.size),
            colorFilter = ColorFilter.tint(tint),
        )
    }

    @Composable
    override fun DirectionPad(p: DirectionPadProps) {
        val horizontalOnly = p.directions.none { it == Direction.Up || it == Direction.Down }
        if (horizontalOnly) {
            Row(p.modifier, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Direction.entries.filter { it in p.directions }.forEach { PadKey(it, p) }
            }
        } else {
            Box(p.modifier.size(168.dp)) {
                val at = mapOf(
                    Direction.Up to Alignment.TopCenter, Direction.Down to Alignment.BottomCenter,
                    Direction.Left to Alignment.CenterStart, Direction.Right to Alignment.CenterEnd,
                )
                p.directions.forEach { d -> Box(Modifier.align(at.getValue(d))) { PadKey(d, p) } }
            }
        }
    }

    @Composable
    private fun PadKey(d: Direction, p: DirectionPadProps) {
        val c = RoseTheme.colors
        var pressed by remember { mutableStateOf(false) }
        Box(
            Modifier.size(56.dp).clip(RoseTheme.shapes.small).background(if (pressed) c.accent else c.surfaceVariant)
                .holdInput(d.label, p.enabled) { pressed = it; p.onDirection(d, it) }.alpha(if (p.enabled) 1f else 0.38f),
            contentAlignment = Alignment.Center,
        ) {
            val icon = when (d) { Direction.Up -> RoseIcon.Up; Direction.Down -> RoseIcon.Down; Direction.Left -> RoseIcon.Left; Direction.Right -> RoseIcon.Right }
            Icon(IconProps(icon, null, tint = if (pressed) c.onAccent else c.onSurfaceVariant, size = 28.dp))
        }
    }

    @Composable
    override fun HoldButton(p: HoldButtonProps) {
        val c = RoseTheme.colors
        var pressed by remember { mutableStateOf(false) }
        Box(
            p.modifier.size(76.dp).clip(CircleShape).background(if (pressed) c.onBackground else c.accent)
                .holdInput(p.label, p.enabled) { pressed = it; p.onPressedChange(it) }.alpha(if (p.enabled) 1f else 0.38f),
            contentAlignment = Alignment.Center,
        ) {
            ProvideContentColor(if (pressed) c.background else c.onAccent) {
                if (p.icon != null) Icon(IconProps(p.icon, null, size = 32.dp)) else Text(p.label, role = TextRole.Label)
            }
        }
    }

    @Composable
    override fun Stat(p: StatProps) {
        val c = RoseTheme.colors
        Column(
            p.modifier.semantics(mergeDescendants = true) {
                p.progress?.let { progressBarRangeInfo = ProgressBarRangeInfo(it.coerceIn(0f, 1f), 0f..1f) }
            },
        ) {
            Text(p.label, role = TextRole.Caption, color = c.muted)
            Text(p.value, role = TextRole.Title)
            p.progress?.let {
                Box(Modifier.padding(top = 4.dp).width(96.dp).height(4.dp).clip(CircleShape).background(c.surfaceVariant)) {
                    Box(Modifier.fillMaxWidth(it.coerceIn(0f, 1f)).fillMaxHeight().background(c.accent))
                }
            }
        }
    }
}
