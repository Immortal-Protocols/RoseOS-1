package app.rose.theme.material3

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.asPaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button as FilledButton
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon as MaterialIcon
import androidx.compose.material3.IconButton as MaterialIconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.ListItem as MaterialListItem
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.LocalContentColor as MaterialContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedIconToggleButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.ripple
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.rose.design.BarAction
import app.rose.design.ButtonKind
import app.rose.design.ButtonProps
import app.rose.design.Controls
import app.rose.design.DirectionPadProps
import app.rose.design.DividerProps
import app.rose.design.HoldButtonProps
import app.rose.design.IconButtonProps
import app.rose.design.IconProps
import app.rose.design.ListItemProps
import app.rose.design.LocalContentColor as RoseContentColor
import app.rose.design.PanelKind
import app.rose.design.PanelProps
import app.rose.design.ProgressProps
import app.rose.design.ProvideContentColor
import app.rose.design.RoseIcons
import app.rose.design.RoseTheme
import app.rose.design.ScreenProps
import app.rose.design.SegmentedProps
import app.rose.design.SliderProps
import app.rose.design.StatProps
import app.rose.design.TextFieldProps
import app.rose.design.ToggleProps
import app.rose.design.minTouchTarget

internal object Material3Controls : Controls {
    @OptIn(ExperimentalMaterial3Api::class)
    @Composable
    override fun Screen(p: ScreenProps) {
        val scheme = MaterialTheme.colorScheme
        Surface(
            modifier = p.modifier.fillMaxSize(),
            color = scheme.background,
            contentColor = scheme.onBackground,
        ) {
            Column {
                TopAppBar(
                    title = {
                        Text(
                            p.title, Modifier.semantics { heading() },
                            maxLines = 1, overflow = TextOverflow.Ellipsis,
                        )
                    },
                    navigationIcon = { p.navigation?.let { BarIcon(it) } },
                    actions = { p.actions.forEach { BarIcon(it) } },
                    colors = TopAppBarDefaults.topAppBarColors(containerColor = scheme.surfaceContainer),
                    windowInsets = WindowInsets.safeDrawing.only(WindowInsetsSides.Top + WindowInsetsSides.Horizontal),
                )
                // The v1 viewport starts below the top bar and ends above any supplied bottom bar.
                val contentSides = if (p.bottomBar == null) {
                    WindowInsetsSides.Horizontal + WindowInsetsSides.Bottom
                } else {
                    WindowInsetsSides.Horizontal
                }
                val padding = WindowInsets.safeDrawing.only(contentSides).asPaddingValues()
                Box(Modifier.weight(1f).fillMaxWidth()) {
                    WithMaterialContentColor { p.content(padding) }
                }
                p.bottomBar?.let { bar ->
                    Surface(color = scheme.surfaceContainer, contentColor = scheme.onSurface) {
                        Box(
                            Modifier.fillMaxWidth().windowInsetsPadding(
                                WindowInsets.safeDrawing.only(WindowInsetsSides.Horizontal + WindowInsetsSides.Bottom),
                            ),
                        ) {
                            WithMaterialContentColor { bar() }
                        }
                    }
                }
            }
        }
    }

    @Composable
    private fun BarIcon(action: BarAction) {
        WithMaterialContentColor {
            IconButton(
                IconButtonProps(
                    action.icon, action.contentDescription, action.onClick,
                    enabled = action.enabled, checked = action.checked,
                ),
            )
        }
    }

    @Composable
    override fun Panel(p: PanelProps) {
        val scheme = MaterialTheme.colorScheme
        val shape = RoseTheme.shapes.medium
        val color = when (p.kind) {
            PanelKind.Raised -> scheme.surfaceContainerLow
            PanelKind.Flat -> scheme.surface
            PanelKind.Inset -> scheme.surfaceContainerHighest
        }
        val border = if (p.kind == PanelKind.Flat) BorderStroke(1.dp, scheme.outlineVariant) else null
        val elevation = if (p.kind == PanelKind.Raised) 1.dp else 0.dp
        val content: @Composable () -> Unit = {
            Box(Modifier.padding(RoseTheme.spacing.l)) {
                WithMaterialContentColor { p.content() }
            }
        }
        val click = p.onClick
        if (click == null) {
            Surface(
                modifier = p.modifier, shape = shape, color = color, contentColor = scheme.onSurface,
                shadowElevation = elevation, border = border, content = content,
            )
        } else {
            Surface(
                onClick = click,
                modifier = p.modifier.minTouchTarget().semantics {
                    role = Role.Button
                    p.onClickLabel?.let { onClick(label = it, action = null) }
                },
                shape = shape, color = color, contentColor = scheme.onSurface,
                shadowElevation = elevation, border = border, content = content,
            )
        }
    }

    @Composable
    override fun Button(p: ButtonProps) {
        val modifier = p.modifier.minTouchTarget()
        val content: @Composable RowScope.() -> Unit = {
            p.icon?.let { MaterialIcon(RoseIcons.vector(it), null, Modifier.size(ButtonDefaults.IconSize)) }
            Text(p.label, Modifier.padding(start = if (p.icon == null) 0.dp else ButtonDefaults.IconSpacing))
        }
        when (p.kind) {
            ButtonKind.Primary -> FilledButton(p.onClick, modifier, enabled = p.enabled, content = content)
            ButtonKind.Secondary -> FilledTonalButton(
                p.onClick, modifier, enabled = p.enabled,
                border = if (p.enabled) tonalControlBorder() else null,
                content = content,
            )
            ButtonKind.Quiet -> TextButton(p.onClick, modifier, enabled = p.enabled, content = content)
            ButtonKind.Danger -> {
                val scheme = MaterialTheme.colorScheme
                FilledButton(
                    p.onClick, modifier, enabled = p.enabled, content = content,
                    colors = ButtonDefaults.buttonColors(containerColor = scheme.error, contentColor = scheme.onError),
                )
            }
        }
    }

    @Composable
    override fun IconButton(p: IconButtonProps) {
        val modifier = if (p.prominent) p.modifier.size(64.dp) else p.modifier.minTouchTarget()
        val image: @Composable () -> Unit = {
            MaterialIcon(
                RoseIcons.vector(p.icon), p.contentDescription,
                Modifier.size(if (p.prominent) 28.dp else 24.dp),
            )
        }
        val checked = p.checked
        when {
            checked != null -> OutlinedIconToggleButton(
                checked, { p.onClick() }, modifier, enabled = p.enabled,
                colors = if (p.prominent) IconButtonDefaults.filledIconToggleButtonColors()
                    else IconButtonDefaults.filledTonalIconToggleButtonColors(),
                border = if (p.enabled && !checked) tonalControlBorder() else null,
                content = image,
            )
            p.prominent -> FilledIconButton(p.onClick, modifier, enabled = p.enabled, content = image)
            else -> MaterialIconButton(p.onClick, modifier, enabled = p.enabled, content = image)
        }
    }

    @Composable
    override fun Toggle(p: ToggleProps) {
        val scheme = MaterialTheme.colorScheme
        Row(
            modifier = p.modifier.minTouchTarget().clip(RoseTheme.shapes.small).toggleable(
                value = p.checked,
                interactionSource = null,
                indication = ripple(color = scheme.onSurface),
                enabled = p.enabled,
                role = Role.Switch,
                onValueChange = p.onCheckedChange,
            ).padding(horizontal = RoseTheme.spacing.s),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(RoseTheme.spacing.m),
        ) {
            Text(p.label, color = if (p.enabled) RoseContentColor.current else scheme.onSurface.copy(alpha = 0.38f))
            Switch(checked = p.checked, onCheckedChange = null, enabled = p.enabled)
        }
    }

    @Composable
    override fun Slider(p: SliderProps) = MaterialTimeline(p)

    @Composable
    override fun Progress(p: ProgressProps) {
        val modifier = p.modifier.semantics { contentDescription = p.label }
        val progress = p.progress
        if (progress == null) LinearProgressIndicator(modifier)
        else LinearProgressIndicator(progress = { progress }, modifier = modifier)
    }

    @Composable
    override fun ListItem(p: ListItemProps) {
        val scheme = MaterialTheme.colorScheme
        val foreground = if (p.selected) scheme.onSecondaryContainer else scheme.onSurface
        var modifier = p.modifier.semantics { selected = p.selected }
        p.onClick?.let {
            modifier = modifier.minTouchTarget().clip(ListItemDefaults.shape).clickable(
                interactionSource = null, indication = ripple(color = foreground),
                role = Role.Button, onClick = it,
            )
        }
        MaterialListItem(
            modifier = modifier,
            headlineContent = { Text(p.headline) },
            supportingContent = p.supporting?.let { { Text(it) } },
            leadingContent = p.leading?.let { leading -> { WithMaterialContentColor { leading() } } }
                ?: p.leadingIcon?.let { icon -> { MaterialIcon(RoseIcons.vector(icon), null) } },
            trailingContent = p.trailing?.let { trailing -> { WithMaterialContentColor { trailing() } } },
            colors = ListItemDefaults.colors(
                containerColor = if (p.selected) scheme.secondaryContainer else scheme.surface,
                headlineColor = foreground,
                supportingColor = if (p.selected) foreground else scheme.onSurfaceVariant,
                leadingIconColor = if (p.selected) foreground else scheme.onSurfaceVariant,
                trailingIconColor = if (p.selected) foreground else scheme.onSurfaceVariant,
            ),
        )
    }

    @Composable
    override fun Segmented(p: SegmentedProps) {
        SingleChoiceSegmentedButtonRow(p.modifier.semantics { contentDescription = p.label }) {
            p.options.forEachIndexed { index, label ->
                SegmentedButton(
                    selected = p.selectedIndex == index,
                    onClick = { p.onSelect(index) },
                    shape = SegmentedButtonDefaults.itemShape(index, p.options.size),
                    modifier = Modifier.minTouchTarget().semantics { role = Role.Tab },
                    enabled = p.enabled,
                    label = { Text(label) },
                )
            }
        }
    }

    @Composable
    override fun TextField(p: TextFieldProps) {
        OutlinedTextField(
            value = p.value,
            onValueChange = p.onValueChange,
            modifier = p.modifier.minTouchTarget(),
            enabled = p.enabled,
            label = { Text(p.label) },
            placeholder = p.placeholder?.let { { Text(it) } },
            singleLine = p.singleLine,
            keyboardOptions = if (p.singleLine && p.onSubmit != null) {
                KeyboardOptions(imeAction = ImeAction.Done)
            } else {
                KeyboardOptions.Default
            },
            keyboardActions = KeyboardActions(onDone = p.onSubmit?.let { submit -> { submit() } }),
        )
    }

    @Composable
    override fun Divider(p: DividerProps) {
        HorizontalDivider(p.modifier)
    }

    @Composable
    override fun Icon(p: IconProps) {
        MaterialIcon(
            RoseIcons.vector(p.icon), p.contentDescription, p.modifier.size(p.size),
            tint = if (p.tint == Color.Unspecified) RoseContentColor.current else p.tint,
        )
    }

    @Composable
    override fun DirectionPad(p: DirectionPadProps) = MaterialDirectionPad(p)

    @Composable
    override fun HoldButton(p: HoldButtonProps) = MaterialHoldButton(p)

    @Composable
    override fun Stat(p: StatProps) {
        Column(
            p.modifier.semantics(mergeDescendants = true) {
                p.progress?.let { progressBarRangeInfo = ProgressBarRangeInfo(it.coerceIn(0f, 1f), 0f..1f) }
            },
            verticalArrangement = Arrangement.spacedBy(RoseTheme.spacing.xs),
        ) {
            Text(p.label, style = MaterialTheme.typography.labelMedium, color = RoseContentColor.current)
            Text(p.value, style = MaterialTheme.typography.titleLarge, color = RoseContentColor.current)
            p.progress?.let {
                Box(Modifier.clearAndSetSemantics {}) {
                    LinearProgressIndicator(progress = { it }, modifier = Modifier.width(96.dp))
                }
            }
        }
    }
}

@Composable
private fun WithMaterialContentColor(content: @Composable () -> Unit) =
    ProvideContentColor(MaterialContentColor.current, content)

/** Material's light tonal fills can have the same luminance as an inset surface. */
@Composable
internal fun tonalControlBorder() =
    BorderStroke(1.dp, MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.8f))
