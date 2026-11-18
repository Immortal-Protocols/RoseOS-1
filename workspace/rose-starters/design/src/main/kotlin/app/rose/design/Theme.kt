package app.rose.design

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.text.selection.LocalTextSelectionColors
import androidx.compose.foundation.text.selection.TextSelectionColors
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.movableContentOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

/**
 * A theme: everything about how a Rose app looks and how its stock controls feel, and nothing about what it does.
 *
 * A theme supplies [tokens] (colour roles, type, shapes, spacing) for light and dark mode, and an implementation of
 * every stock control ([controls]). It never sees a template, and a template never sees a theme: both meet only in
 * this package, which is why any theme applies to any template with one line and no adapter:
 *
 * ```
 * RoseTheme(MinimalTheme) { PlayerTemplate.App() }
 * ```
 *
 * Implement it as a Kotlin `object` in its own module (`app.rose.theme.<id>`), depending on `:design` only, plus
 * whatever its look needs (the Material 3 theme depends on Material 3; nothing else does).
 */
interface Theme {
    /** Stable lowercase id, e.g. "minimal". Rose's builder asks for a theme by this id. */
    val id: String

    /** Shown to people, e.g. "Minimal". */
    val displayName: String

    /** False only for an unimplemented placeholder: the conformance kit and the matrix skip it. */
    val implemented: Boolean get() = true

    /** The theme's tokens for one mode. Called rarely (once per mode change); return the same values for the same mode. */
    fun tokens(dark: Boolean): RoseTokens

    /** Every stock control, drawn this theme's way. */
    val controls: Controls

    /**
     * Wraps the whole app once, inside [RoseTheme], with the tokens already provided. A theme that builds on another
     * design system sets it up here (the Material 3 theme provides MaterialTheme, its ripple and its colour scheme).
     * Most themes keep the default.
     */
    @Composable
    fun Provide(tokens: RoseTokens, content: @Composable () -> Unit) {
        content()
    }
}

internal val LocalRoseTokens = staticCompositionLocalOf<RoseTokens?> { null }
internal val LocalRoseControls = staticCompositionLocalOf<Controls?> { null }
internal val LocalRoseThemeId = staticCompositionLocalOf<String?> { null }

/**
 * Applies [theme] to [content]: the one line that hooks a template up to a theme.
 *
 * @param dark light or dark mode. Defaults to the system setting; light and dark are modes of one theme, not two themes.
 */
@Composable
fun RoseTheme(
    theme: Theme,
    dark: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    val tokens = remember(theme, dark) { theme.tokens(dark) }
    // Movable, so swapping to a theme whose Provide differs keeps the template's remembered state (scroll position,
    // a game in progress) instead of starting the app over.
    val app = remember { movableContentOf { c: @Composable () -> Unit -> c() } }
    val selection = remember(tokens) {
        TextSelectionColors(handleColor = tokens.colors.accent, backgroundColor = tokens.colors.accent.copy(alpha = 0.35f))
    }
    CompositionLocalProvider(
        LocalRoseTokens provides tokens,
        LocalRoseControls provides theme.controls,
        LocalRoseThemeId provides theme.id,
        LocalContentColor provides tokens.colors.onBackground,
        LocalTextSelectionColors provides selection,
    ) {
        theme.Provide(tokens) { app(content) }
    }
}

/** What a template (or a theme's own control) reads about the current theme. Only valid inside [RoseTheme]. */
object RoseTheme {
    val tokens: RoseTokens
        @Composable @ReadOnlyComposable
        get() = LocalRoseTokens.current ?: error("No RoseTheme: wrap the app in RoseTheme(SomeTheme) { … }")

    val colors: RoseColors @Composable @ReadOnlyComposable get() = tokens.colors
    val type: RoseType @Composable @ReadOnlyComposable get() = tokens.type
    val shapes: RoseShapes @Composable @ReadOnlyComposable get() = tokens.shapes
    val spacing: RoseSpacing @Composable @ReadOnlyComposable get() = tokens.spacing

    /** The current theme's id, e.g. for analytics or a screenshot name. Templates must not branch on it. */
    val id: String
        @Composable @ReadOnlyComposable
        get() = LocalRoseThemeId.current ?: error("No RoseTheme: wrap the app in RoseTheme(SomeTheme) { … }")

    internal val controls: Controls
        @Composable @ReadOnlyComposable
        get() = LocalRoseControls.current ?: error("No RoseTheme: wrap the app in RoseTheme(SomeTheme) { … }")
}

/**
 * The colour [Text] and [Icon] use when given none. A theme's surface, button or panel sets it for its content, the
 * way a Material surface sets its content colour.
 */
val LocalContentColor = staticCompositionLocalOf { Color.Black }
