package app.rose.starters.matrix

import androidx.compose.ui.test.junit4.createComposeRule
import app.rose.design.Theme
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.setThemedContent
import app.rose.template.player.PlayerTemplate
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * OWNER: Player template engineer (this file only). The template's main interaction under every pilot theme, light
 * and dark, API 34 and 37: the same script as PlayerTemplateBehaviorTest, unchanged per theme. A theme that is still a
 * placeholder is skipped, not passed.
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37], qualifiers = "w411dp-h891dp")
class PlayerScenario(@Suppress("unused") private val id: String, private val theme: Theme, private val dark: Boolean) {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun mainInteraction() {
        assumeTrue("PlayerTemplate is a placeholder", PlayerTemplate.implemented)
        assumeTrue("${theme.id} is a placeholder", theme.implemented)
        rule.setThemedContent(theme, dark) { PlayerTemplate.App() }
        // TODO(Player template engineer): the main interaction, as in PlayerTemplateBehaviorTest.
    }

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "{0} dark={2}")
        fun cases() = ThemeCases.of(*Pilots.themes.toTypedArray())
    }
}
