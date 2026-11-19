package app.rose.template.spritegame

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.junit4.StateRestorationTester
import androidx.compose.ui.test.junit4.v2.createComposeRule
import app.rose.design.BaselineTheme
import app.rose.design.RoseTheme
import app.rose.design.Theme
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.setThemedContent
import app.rose.starters.examples.PaperTheme
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config

/** Play the same scene under the two reference themes, in both modes and on both Android targets. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37], qualifiers = "w411dp-h891dp")
class SpriteGameTemplateBehaviorTest(@Suppress("unused") private val id: String, private val theme: Theme, private val dark: Boolean) {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun mainInteraction() {
        rule.setThemedContent(theme, dark) { SpriteGameTemplate.App() }
        assertPosition(2, 2)
        rule.onNodeWithText("0 / 3").assertExists()
        move("Left")
        assertPosition(2, 2) // The wall stops a step.

        val down = rule.onNodeWithContentDescription("Down")
        down.performTouchInput { down(center) }
        assertPosition(2, 3)
        rule.mainClock.autoAdvance = false
        try {
            rule.mainClock.advanceTimeBy(288)
            assertPosition(2, 4) // Held input repeats after the initial step.
            down.performTouchInput { up() }
            rule.mainClock.advanceTimeBy(500)
            assertPosition(2, 4) // Release stops movement before the edge.
        } finally {
            rule.mainClock.autoAdvance = true
        }

        move("Down", 2)
        rule.onNodeWithText("1 / 3").assertExists()
        move("Right", 3)
        rule.onNodeWithText("Signal lost").assertExists()
        assertPosition(5, 6)
        rule.onNodeWithContentDescription("Right").assertIsNotEnabled()

        restart()
        move("Right", 2)
        move("Down", 2)
        move("Right", 4)
        rule.onNodeWithText("2 / 3").assertExists()
        move("Up", 2)
        assertPosition(8, 3) // The locked gate stops the second step.

        move("Down")
        move("Left", 6)
        move("Down", 2)
        rule.onNodeWithText("3 / 3").assertExists()
        rule.onNodeWithText("Gate open").assertExists()
        move("Up", 2)
        move("Right", 6)
        move("Up", 2)
        rule.onNodeWithText("Complete").assertExists()
        assertPosition(8, 2)

        restart()
    }

    @Test
    fun roundSurvivesRestorationAndThemeSwap() {
        var activeTheme by mutableStateOf(theme)
        val restoration = StateRestorationTester(rule)
        restoration.setContent { RoseTheme(activeTheme, dark) { SpriteGameTemplate.App() } }
        move("Right", 2)
        restoration.emulateSavedInstanceStateRestore()
        assertPosition(4, 2)
        rule.onNodeWithText("1 / 3").assertExists()

        rule.runOnIdle { activeTheme = if (theme == BaselineTheme) PaperTheme else BaselineTheme }
        assertPosition(4, 2)
        rule.onNodeWithText("1 / 3").assertExists()

        move("Down", 2)
        move("Right")
        rule.onNodeWithText("2 / 3").assertExists()
    }

    private fun move(direction: String, steps: Int = 1) {
        repeat(steps) { rule.onNodeWithContentDescription(direction).performClick() }
    }

    private fun assertPosition(column: Int, row: Int) {
        val position = "Column $column, row $row."
        val playfield = rule.onNodeWithContentDescription("Playfield").fetchSemanticsNode()
        val description = playfield.config[SemanticsProperties.StateDescription]
        assertTrue("Expected $position Playfield reported $description", description.startsWith(position))
    }

    private fun restart() {
        rule.onNodeWithText("Restart").performClick()
        assertPosition(2, 2)
        rule.onNodeWithText("0 / 3").assertExists()
        rule.onNodeWithText("Exploring").assertExists()
    }

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "{0} dark={2}")
        fun cases() = ThemeCases.of(BaselineTheme, PaperTheme)
    }
}
