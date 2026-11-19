package app.rose.starters.matrix

import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.junit4.v2.createComposeRule
import app.rose.design.Theme
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.setThemedContent
import app.rose.template.spritegame.SpriteGameTemplate
import org.junit.Assume.assumeTrue
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The same playthrough as SpriteGameTemplateBehaviorTest under each implemented pilot theme.
 * Placeholder themes are skipped.
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37], qualifiers = "w411dp-h891dp")
class SpriteGameScenario(@Suppress("unused") private val id: String, private val theme: Theme, private val dark: Boolean) {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun mainInteraction() {
        assumeTrue("${theme.id} is a placeholder", theme.implemented)
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
        fun cases() = ThemeCases.of(*Pilots.themes.toTypedArray())
    }
}
