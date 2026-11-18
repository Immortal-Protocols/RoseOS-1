package app.rose.theme.material3

import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import app.rose.design.Controls
import app.rose.design.RoseColors
import app.rose.design.RoseShapes
import app.rose.design.RoseTokens
import app.rose.design.RoseType
import app.rose.design.Theme

/** The native Material 3 starter, with a stable color scheme in each mode. */
object Material3Theme : Theme {
    override val id = "material3"
    override val displayName = "Material 3"
    override val implemented = true
    override val controls: Controls = Material3Controls

    private val lightScheme = lightColorScheme()
    private val darkScheme = darkColorScheme()
    private val typography = Typography()
    private val shapes = Shapes()
    private val lightTokens = lightScheme.roseTokens(dark = false)
    private val darkTokens = darkScheme.roseTokens(dark = true)

    override fun tokens(dark: Boolean): RoseTokens = if (dark) darkTokens else lightTokens

    @Composable
    override fun Provide(tokens: RoseTokens, content: @Composable () -> Unit) {
        MaterialTheme(
            colorScheme = if (tokens.colors.isDark) darkScheme else lightScheme,
            typography = typography,
            shapes = shapes,
            content = content,
        )
    }

    private fun ColorScheme.roseTokens(dark: Boolean) = RoseTokens(
        colors = RoseColors(
            background = background,
            onBackground = onBackground,
            surface = surface,
            onSurface = onSurface,
            muted = onSurfaceVariant,
            surfaceVariant = surfaceVariant,
            onSurfaceVariant = onSurfaceVariant,
            accent = primary,
            onAccent = onPrimary,
            outline = outline,
            danger = error,
            onDanger = onError,
            isDark = dark,
        ),
        type = RoseType(
            display = typography.displaySmall,
            title = typography.titleLarge,
            body = typography.bodyLarge,
            label = typography.labelLarge,
            caption = typography.bodySmall,
        ),
        shapes = RoseShapes(small = shapes.small, medium = shapes.medium, large = shapes.large),
    )
}
