package app.rose.starters.matrix

import androidx.compose.ui.test.junit4.createComposeRule
import app.rose.design.Theme
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.setThemedContent
import app.rose.template.spritegame.SpriteGameTemplate
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * OWNER: Sprite game template engineer (this file only). The template's main interaction under every pilot theme, light
 * and dark, API 34 and 37: the same script as SpriteGameTemplateBehaviorTest, unchanged per theme. A theme that is still a
 * placeholder is skipped, not passed.
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37], qualifiers = "w411dp-h891dp")
class SpriteGameScenario(@Suppress("unused") private val id: String, private val theme: Theme, private val dark: Boolean) {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun mainInteraction() {
        assumeTrue("SpriteGameTemplate is a placeholder", SpriteGameTemplate.implemented)
        assumeTrue("${theme.id} is a placeholder", theme.implemented)
        rule.setThemedContent(theme, dark) { SpriteGameTemplate.App() }
        // TODO(Sprite game template engineer): the main interaction, as in SpriteGameTemplateBehaviorTest.
    }

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "{0} dark={2}")
        fun cases() = ThemeCases.of(*Pilots.themes.toTypedArray())
    }
}
