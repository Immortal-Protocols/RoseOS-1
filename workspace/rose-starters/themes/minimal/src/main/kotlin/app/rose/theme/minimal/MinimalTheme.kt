package app.rose.theme.minimal

import app.rose.design.BaselineControls
import app.rose.design.BaselineTheme
import app.rose.design.Controls
import app.rose.design.RoseTokens
import app.rose.design.Theme

/**
 * OWNER: Minimal theme engineer. Typography, spacing and restrained controls; selection, progress and touch targets stay clear.
 *
 * The Minimal theme. Keep the object and its [id]; replace the placeholder tokens and controls with your own
 * (implement every member of Controls; :examples' PaperTheme and app.rose.design.BaselineControls show the pattern,
 * ControlKit.kt has input helpers). Set [implemented] to true when MinimalThemeConformance passes.
 */
object MinimalTheme : Theme {
    override val id = "minimal"
    override val displayName = "Minimal"
    override val implemented = false // placeholder: conformance and the matrix skip it until this is true

    override fun tokens(dark: Boolean): RoseTokens = BaselineTheme.tokens(dark)
    override val controls: Controls = BaselineControls
}
