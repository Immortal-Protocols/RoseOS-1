package app.rose.design.testing

import androidx.compose.runtime.Composable
import androidx.compose.ui.test.junit4.ComposeContentTestRule
import app.rose.design.RoseTheme
import app.rose.design.Theme

/** Parameters for ParameterizedRobolectricTestRunner: the same test under every theme and both modes. */
object ThemeCases {
    /** Light and dark: `{0}` is `dark`. */
    @JvmStatic
    fun modes(): List<Array<Any>> = listOf(arrayOf(false), arrayOf(true))

    /** Every given theme in light and dark: `{0}` is the theme id, `{1}` the Theme, `{2}` `dark`. */
    @JvmStatic
    fun of(vararg themes: Theme): List<Array<Any>> =
        themes.flatMap { t -> listOf(false, true).map { dark -> arrayOf(t.id, t, dark) } }
}

/** Sets [content] inside `RoseTheme(theme, dark)`: how a template's behaviour test runs under each theme. */
fun ComposeContentTestRule.setThemedContent(theme: Theme, dark: Boolean, content: @Composable () -> Unit) =
    setContent { RoseTheme(theme, dark) { content() } }
