package app.rose.theme.material3

import app.rose.design.BaselineControls
import app.rose.design.BaselineTheme
import app.rose.design.Controls
import app.rose.design.RoseTokens
import app.rose.design.Theme

/**
 * OWNER: Material 3 theme engineer. Reusable controls and surfaces with native Material interaction states.
 *
 * The Material 3 theme. Keep the object and its [id]; replace the placeholder tokens and controls with your own
 * (implement every member of Controls; :examples' PaperTheme and app.rose.design.BaselineControls show the pattern,
 * ControlKit.kt has input helpers). Set [implemented] to true when Material3ThemeConformance passes.
 */
object Material3Theme : Theme {
    override val id = "material3"
    override val displayName = "Material 3"
    override val implemented = false // placeholder: conformance and the matrix skip it until this is true

    override fun tokens(dark: Boolean): RoseTokens = BaselineTheme.tokens(dark)
    override val controls: Controls = BaselineControls
}
