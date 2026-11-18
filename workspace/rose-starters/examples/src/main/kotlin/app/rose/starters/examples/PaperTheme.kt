package app.rose.starters.examples

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.rose.design.BaselineControls
import app.rose.design.ButtonKind
import app.rose.design.ButtonProps
import app.rose.design.Controls
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
import app.rose.design.RoseColors
import app.rose.design.RoseShapes
import app.rose.design.RoseTheme
import app.rose.design.RoseTokens
import app.rose.design.RoseType
import app.rose.design.ScreenProps
import app.rose.design.SegmentedProps
import app.rose.design.SliderProps
import app.rose.design.StatProps
import app.rose.design.Text
import app.rose.design.TextFieldProps
import app.rose.design.TextRole
import app.rose.design.Theme
import app.rose.design.ToggleProps
import app.rose.design.minTouchTarget
import app.rose.design.sliderFraction
import app.rose.design.sliderInput

/**
 * The sample's second theme: ink on paper. Serif type, square corners, ruled lines, a checkbox where Baseline has a
 * switch. It shows the whole theme hookup a pilot theme needs: tokens for light and dark, and every member of
 * [Controls]. It draws the controls whose look defines it and hands the rest to [BaselineControls], which still read
 * Paper's tokens. A pilot theme draws all of them. Not one of the pilot themes.
 */
object PaperTheme : Theme {
    override val id = "paper"
    override val displayName = "Paper"
    override val controls: Controls = PaperControls

    private val light = RoseColors(
        background = Color(0xFFF4EEDF), onBackground = Color(0xFF231F1A),
        surface = Color(0xFFFBF7EC), onSurface = Color(0xFF231F1A), muted = Color(0xFF6A5F52),
        surfaceVariant = Color(0xFFE7DEC8), onSurfaceVariant = Color(0xFF2E2922),
        accent = Color(0xFF1F4E79), onAccent = Color(0xFFFBF7EC), outline = Color(0xFF231F1A),
        danger = Color(0xFF9E2A1E), onDanger = Color(0xFFFBF7EC), isDark = false,
    )
    private val dark = RoseColors(
        background = Color(0xFF1E1B17), onBackground = Color(0xFFEDE5D3),
        surface = Color(0xFF26221D), onSurface = Color(0xFFEDE5D3), muted = Color(0xFFB4A994),
        surfaceVariant = Color(0xFF332E27), onSurfaceVariant = Color(0xFFEDE5D3),
        accent = Color(0xFF9EC3E6), onAccent = Color(0xFF14212D), outline = Color(0xFFEDE5D3),
        danger = Color(0xFFF2A397), onDanger = Color(0xFF3B0D07), isDark = true,
    )
    private val type = RoseType(
        display = TextStyle(fontFamily = FontFamily.Serif, fontWeight = FontWeight.Bold, fontSize = 40.sp, lineHeight = 46.sp),
        title = TextStyle(fontFamily = FontFamily.Serif, fontWeight = FontWeight.Bold, fontSize = 21.sp, lineHeight = 26.sp),
        body = TextStyle(fontFamily = FontFamily.Serif, fontSize = 17.sp, lineHeight = 24.sp),
        label = TextStyle(fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Medium, fontSize = 13.sp, lineHeight = 18.sp, letterSpacing = 0.6.sp),
        caption = TextStyle(fontFamily = FontFamily.Serif, fontStyle = FontStyle.Italic, fontSize = 13.sp, lineHeight = 18.sp),
    )
    private val shapes = RoseShapes(small = RoundedCornerShape(2.dp), medium = RoundedCornerShape(3.dp), large = RoundedCornerShape(4.dp))

    override fun tokens(dark: Boolean) = RoseTokens(colors = if (dark) this.dark else light, type = type, shapes = shapes)
}

private object PaperControls : Controls {
    // Drawn Paper's way.

    @Composable
    override fun Panel(p: PanelProps) {
        val c = RoseTheme.colors
        val shape = RoseTheme.shapes.medium
        var m = p.modifier
        m = when (p.kind) {
            PanelKind.Raised -> m.drawBehind { drawRect(c.outline, Offset(3.dp.toPx(), 3.dp.toPx()), size) }.background(c.surface, shape).border(1.5.dp, c.outline, shape)
            PanelKind.Flat -> m.background(c.background).border(1.dp, c.outline.copy(alpha = 0.7f), shape)
            PanelKind.Inset -> m.background(c.surfaceVariant).drawBehind {
                drawRect(c.outline, style = Stroke(1.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f, 6f))))
            }
        }
        p.onClick?.let { m = m.clickable(onClickLabel = p.onClickLabel, role = Role.Button, onClick = it) }
        Box(m.padding(RoseTheme.spacing.l)) { ProvideContentColor(if (p.kind == PanelKind.Inset) c.onSurfaceVariant else c.onSurface) { p.content() } }
    }

    @Composable
    override fun Button(p: ButtonProps) {
        val c = RoseTheme.colors
        val interaction = remember { MutableInteractionSource() }
        val pressed by interaction.collectIsPressedAsState()
        val (fill, ink) = when (p.kind) {
            ButtonKind.Primary -> c.accent to c.onAccent
            ButtonKind.Secondary -> c.surface to c.onSurface
            ButtonKind.Quiet -> Color.Transparent to c.accent
            ButtonKind.Danger -> c.surface to c.danger
        }
        val lift = if (pressed || p.kind == ButtonKind.Quiet) 0.dp else 3.dp
        Box(
            p.modifier.minTouchTarget()
                .clickable(interaction, indication = null, enabled = p.enabled, role = Role.Button, onClick = p.onClick)
                .alpha(if (p.enabled) 1f else 0.4f),
            contentAlignment = Alignment.Center,
        ) {
            Row(
                Modifier.offset(x = 3.dp - lift, y = 3.dp - lift)
                    .drawBehind { if (lift > 0.dp) drawRect(c.outline, Offset(lift.toPx(), lift.toPx()), size) }
                    .background(fill).then(if (p.kind == ButtonKind.Quiet) Modifier else Modifier.border(1.5.dp, if (p.kind == ButtonKind.Danger) c.danger else c.outline))
                    .heightIn(min = 44.dp).padding(horizontal = 18.dp, vertical = 10.dp),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                ProvideContentColor(ink) {
                    p.icon?.let { app.rose.design.Icon(it, null, Modifier.padding(end = 8.dp), size = 16.dp) }
                    Text(p.label, role = TextRole.Label)
                }
            }
        }
    }

    @Composable
    override fun IconButton(p: IconButtonProps) {
        val c = RoseTheme.colors
        val on = p.checked == true
        val ink = if (p.prominent || on) c.onAccent else LocalContentColor.current
        var m = p.modifier.size(if (p.prominent) 60.dp else 48.dp)
        val checked = p.checked
        m = if (checked != null) m.toggleable(checked, enabled = p.enabled, role = Role.Checkbox) { p.onClick() }
        else m.clickable(enabled = p.enabled, role = Role.Button, onClick = p.onClick)
        m = m.semantics { contentDescription = p.contentDescription }.padding(4.dp)
            .background(if (p.prominent || on) c.accent else Color.Transparent)
            .border(1.5.dp, if (p.prominent || on) c.accent else c.outline.copy(alpha = 0.8f))
        Box(m.alpha(if (p.enabled) 1f else 0.4f), contentAlignment = Alignment.Center) {
            app.rose.design.Icon(p.icon, null, tint = ink, size = if (p.prominent) 28.dp else 22.dp)
        }
    }

    @Composable
    override fun Toggle(p: ToggleProps) {
        val c = RoseTheme.colors
        Row(
            p.modifier.fillMaxWidth().toggleable(p.checked, enabled = p.enabled, role = Role.Switch, onValueChange = p.onCheckedChange)
                .heightIn(min = 48.dp).alpha(if (p.enabled) 1f else 0.4f),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.size(24.dp).border(1.5.dp, c.outline).background(if (p.checked) c.accent else Color.Transparent), contentAlignment = Alignment.Center) {
                if (p.checked) app.rose.design.Icon(app.rose.design.RoseIcon.Check, null, tint = c.onAccent, size = 18.dp)
            }
            Text(p.label, Modifier.padding(start = 12.dp))
        }
    }

    @Composable
    override fun Slider(p: SliderProps) {
        val c = RoseTheme.colors
        val f = sliderFraction(p.value, p.valueRange)
        val ticks = if (p.steps > 0) p.steps + 2 else 11
        Box(p.modifier.fillMaxWidth().heightIn(min = 48.dp).sliderInput(p, thumbInset = 8.dp).alpha(if (p.enabled) 1f else 0.4f)) {
            Canvas(Modifier.fillMaxWidth().height(48.dp)) {
                val inset = 8.dp.toPx()
                val w = size.width - 2 * inset
                val y = size.height / 2
                fun x(v: Float) = inset + v * w
                p.highlight?.let {
                    val a = x(sliderFraction(it.start, p.valueRange)); val b = x(sliderFraction(it.endInclusive, p.valueRange))
                    drawRect(c.accent.copy(alpha = 0.22f), Offset(a, y - 10.dp.toPx()), Size(b - a, 20.dp.toPx()))
                }
                drawLine(c.outline, Offset(inset, y), Offset(inset + w, y), 1.5.dp.toPx())
                repeat(ticks) { i -> val tx = x(i / (ticks - 1f)); drawLine(c.outline, Offset(tx, y - 5.dp.toPx()), Offset(tx, y + 5.dp.toPx()), 1.dp.toPx()) }
                drawLine(c.accent, Offset(inset, y), Offset(x(f), y), 4.dp.toPx())
                p.marks.forEach { m -> val mx = x(sliderFraction(m, p.valueRange)); drawLine(c.danger, Offset(mx, y - 12.dp.toPx()), Offset(mx, y + 12.dp.toPx()), 2.dp.toPx()) }
                val t = 8.dp.toPx()
                drawRect(c.surface, Offset(x(f) - t, y - t), Size(2 * t, 2 * t))
                drawRect(c.outline, Offset(x(f) - t, y - t), Size(2 * t, 2 * t), style = Stroke(1.5.dp.toPx()))
            }
        }
    }

    @Composable
    override fun Progress(p: ProgressProps) {
        val c = RoseTheme.colors
        Box(
            p.modifier.fillMaxWidth().height(10.dp).border(1.dp, c.outline).padding(2.dp).semantics {
                contentDescription = p.label
                progressBarRangeInfo = p.progress?.let { ProgressBarRangeInfo(it.coerceIn(0f, 1f), 0f..1f) } ?: ProgressBarRangeInfo.Indeterminate
            },
        ) {
            Box(Modifier.fillMaxWidth((p.progress ?: 0.35f).coerceIn(0f, 1f)).fillMaxHeight().background(c.accent))
        }
    }

    @Composable
    override fun Segmented(p: SegmentedProps) {
        val c = RoseTheme.colors
        Row(p.modifier.fillMaxWidth().semantics { contentDescription = p.label }.selectableGroup().alpha(if (p.enabled) 1f else 0.4f)) {
            p.options.forEachIndexed { i, option ->
                val on = i == p.selectedIndex
                Box(
                    Modifier.weight(1f).heightIn(min = 48.dp).selectable(on, enabled = p.enabled, role = Role.Tab) { p.onSelect(i) }
                        .drawBehind {
                            val h = if (on) 3.dp.toPx() else 1.dp.toPx()
                            drawRect(if (on) c.accent else c.outline.copy(alpha = 0.5f), Offset(0f, size.height - h), Size(size.width, h))
                        },
                    contentAlignment = Alignment.Center,
                ) { Text(option, role = TextRole.Label, color = if (on) c.accent else c.muted) }
            }
        }
    }

    // Baseline's drawing, in Paper's tokens.
    @Composable override fun Screen(p: ScreenProps) = BaselineControls.Screen(p)
    @Composable override fun ListItem(p: ListItemProps) = BaselineControls.ListItem(p)
    @Composable override fun TextField(p: TextFieldProps) = BaselineControls.TextField(p)
    @Composable override fun Divider(p: DividerProps) = BaselineControls.Divider(p)
    @Composable override fun Icon(p: IconProps) = BaselineControls.Icon(p)
    @Composable override fun DirectionPad(p: DirectionPadProps) = BaselineControls.DirectionPad(p)
    @Composable override fun HoldButton(p: HoldButtonProps) = BaselineControls.HoldButton(p)
    @Composable override fun Stat(p: StatProps) = BaselineControls.Stat(p)
}
