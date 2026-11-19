package app.rose.design

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier

/**
 * A template: a reusable starting app that owns behaviour and state (what the screens do, what they remember) and
 * draws only with the stock controls and tokens of this package. It never names a theme, so under any [Theme]:
 *
 * ```
 * setContent { RoseTheme(SkeuomorphicTheme) { FeedTemplate.App() } }
 * ```
 *
 * Implement it as a Kotlin `object` in its own module (`app.rose.template.<id>`) depending on `:design` only. [App] is
 * the whole starter with its own sample content and state (kept across configuration changes with
 * rememberSaveable or a ViewModel); the module may also export its screens and state types for Rose to customise.
 * Starters are a starting point Rose can build from, not a format every app must follow.
 */
interface Template {
    /** Stable lowercase id, e.g. "feed". Rose's builder asks for a template by this id. */
    val id: String

    /** Shown to people, e.g. "Feed". */
    val displayName: String

    /** False only for an unimplemented placeholder: the matrix skips it. */
    val implemented: Boolean get() = true

    /** The complete starter app. Call it inside [RoseTheme]. */
    @Composable
    fun App(modifier: Modifier = Modifier)
}
