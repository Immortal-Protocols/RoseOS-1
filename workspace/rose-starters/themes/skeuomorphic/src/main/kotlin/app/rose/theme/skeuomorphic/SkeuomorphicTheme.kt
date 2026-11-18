package app.rose.theme.skeuomorphic

import app.rose.design.BaselineControls
import app.rose.design.BaselineTheme
import app.rose.design.Controls
import app.rose.design.RoseTokens
import app.rose.design.Theme

/**
 * OWNER: Skeuomorphic theme engineer. Physical cues (depth, tracks, pressed states) that help someone operate controls; no template-specific forks.
 *
 * The Skeuomorphic theme. Keep the object and its [id]; replace the placeholder tokens and controls with your own
 * (implement every member of Controls; :examples' PaperTheme and app.rose.design.BaselineControls show the pattern,
 * ControlKit.kt has input helpers). Set [implemented] to true when SkeuomorphicThemeConformance passes.
 */
object SkeuomorphicTheme : Theme {
    override val id = "skeuomorphic"
    override val displayName = "Skeuomorphic"
    override val implemented = false // placeholder: conformance and the matrix skip it until this is true

    override fun tokens(dark: Boolean): RoseTokens = BaselineTheme.tokens(dark)
    override val controls: Controls = BaselineControls
}
