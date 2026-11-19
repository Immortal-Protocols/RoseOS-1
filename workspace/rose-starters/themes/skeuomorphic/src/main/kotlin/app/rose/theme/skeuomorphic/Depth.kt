package app.rose.theme.skeuomorphic

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.interaction.collectIsHoveredAsState
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.unit.dp
import app.rose.design.RoseTheme

// A raking light on the face and a higher-contrast metal edge beneath it make travel visible.
private val shadow = Color.Black
private val light = Color.White

internal fun Modifier.recessedWell(base: Color, edge: Color, shape: Shape): Modifier = clip(shape)
    .background(Brush.verticalGradient(listOf(lerp(base, shadow, 0.18f), base, lerp(base, light, 0.07f))))
    .border(1.dp, Brush.verticalGradient(listOf(lerp(edge, shadow, 0.28f), edge, lerp(edge, light, 0.35f))), shape)

@Composable
internal fun Modifier.keycap(
    fill: Color,
    shape: Shape,
    interaction: MutableInteractionSource,
    latched: Boolean = false,
    shallow: Boolean = false,
): Modifier {
    val pressed by interaction.collectIsPressedAsState()
    val focused by interaction.collectIsFocusedAsState()
    val hovered by interaction.collectIsHoveredAsState()
    return keycap(fill, shape, pressed || latched, focused, hovered, shallow)
}

@Composable
internal fun Modifier.keycap(
    fill: Color,
    shape: Shape,
    pressed: Boolean = false,
    focused: Boolean = false,
    hovered: Boolean = false,
    shallow: Boolean = false,
): Modifier {
    val colors = RoseTheme.colors
    val depth = if (shallow) 2.dp else 5.dp
    val travel = if (pressed) depth - 1.dp else 0.dp
    val top = lerp(fill, light, if (hovered) 0.14f else 0.08f)
    val bottom = lerp(fill, shadow, if (pressed) 0.12f else 0.05f)
    val side = lerp(fill, shadow, if (colors.isDark) 0.64f else 0.41f)
    val rim = if (pressed) {
        listOf(lerp(side, shadow, 0.40f), lerp(fill, light, 0.27f))
    } else {
        listOf(lerp(fill, light, 0.57f), side)
    }
    // The top and bottom padding always sum to depth, so controls and their hit areas stay put.
    val focusInk = if (fill.luminance() > 0.25f) shadow else light
    return clip(shape)
        .background(Brush.verticalGradient(listOf(lerp(side, shadow, 0.20f), side)))
        .padding(top = travel, bottom = depth - travel)
        .clip(shape)
        .background(Brush.verticalGradient(listOf(if (pressed) bottom else top, if (pressed) fill else bottom)))
        .border(1.dp, Brush.verticalGradient(rim), shape)
        .then(if (focused) Modifier.border(2.dp, focusInk, shape) else Modifier)
}

internal fun Modifier.enamel(fill: Color): Modifier = background(
    Brush.linearGradient(
        listOf(lerp(fill, light, 0.08f), fill, lerp(fill, shadow, 0.07f)),
        start = Offset.Zero,
        end = Offset.Infinite,
    ),
)
