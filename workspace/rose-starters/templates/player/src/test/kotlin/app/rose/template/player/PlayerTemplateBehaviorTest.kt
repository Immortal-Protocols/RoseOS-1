package app.rose.template.player

import androidx.compose.ui.test.junit4.createComposeRule
import app.rose.design.BaselineTheme
import app.rose.design.Theme
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.setThemedContent
import app.rose.starters.examples.PaperTheme
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * OWNER: Player template engineer. Write the main interaction once here, driving the app only by what a person sees and does
 * (text, content descriptions, taps, drags), and assert your own state. It runs under Baseline and Paper now; the
 * matrix runs the same kind of script under every pilot theme (matrix/src/test/.../PlayerScenario.kt).
 * See :sample's TallyBehaviorTest for the pattern.
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37], qualifiers = "w411dp-h891dp")
class PlayerTemplateBehaviorTest(@Suppress("unused") private val id: String, private val theme: Theme, private val dark: Boolean) {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun mainInteraction() {
        assumeTrue("Player template is a placeholder", PlayerTemplate.implemented)
        rule.setThemedContent(theme, dark) { PlayerTemplate.App() }
        // TODO(Player template engineer): the main interaction.
    }

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "{0} dark={2}")
        fun cases() = ThemeCases.of(BaselineTheme, PaperTheme)
    }
}
