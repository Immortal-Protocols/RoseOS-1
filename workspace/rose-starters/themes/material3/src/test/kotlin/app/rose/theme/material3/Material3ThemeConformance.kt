package app.rose.theme.material3

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.height
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.assertIsFocused
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performKeyInput
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.pressKey
import androidx.compose.ui.unit.dp
import app.rose.design.BaselineTheme
import app.rose.design.Direction
import app.rose.design.DirectionPad
import app.rose.design.HoldButton
import app.rose.design.RoseTheme
import app.rose.design.Slider
import app.rose.design.Theme
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.ThemeConformanceTest
import app.rose.design.testing.setThemedContent
import app.rose.starters.examples.TallyTemplate
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37])
class Material3ThemeConformance(dark: Boolean) : ThemeConformanceTest(Material3Theme, dark) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun modes() = ThemeCases.modes()
    }
}

@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37], qualifiers = "w411dp-h891dp")
class Material3ThemeBehaviorTest(private val dark: Boolean) {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun tallyKeepsItsStateWhenMaterialAndModeChange() {
        var theme by mutableStateOf<Theme>(BaselineTheme)
        var currentDark by mutableStateOf(dark)
        rule.setContent {
            RoseTheme(theme, dark = currentDark) { TallyTemplate.App() }
        }
        rule.onNodeWithContentDescription("Increase Apples").performClick().performClick()
        rule.onNodeWithText("2 of 10").assertExists()

        rule.runOnIdle { theme = Material3Theme }
        rule.onNodeWithText("2 of 10").assertExists()
        rule.onNodeWithContentDescription("Increase Apples").performClick()
        rule.onNodeWithText("3 of 10").assertExists()

        rule.runOnIdle { currentDark = !currentDark }
        rule.onNodeWithText("3 of 10").assertExists()
        rule.onNodeWithText("Apples").performClick()
        rule.onNodeWithText("Add 1").performClick()
        rule.onNodeWithContentDescription("Back").performClick()
        rule.onNodeWithText("4 of 10").assertExists()
    }

    @Test
    fun signedSteppedSliderSnapsAndDisabledSliderOmitsAccessibilityAction() {
        var value by mutableFloatStateOf(0f)
        var finished = 0
        rule.setThemedContent(Material3Theme, dark) {
            Column {
                Slider(
                    value, { value = it }, "Balance", valueRange = -10f..10f, steps = 3,
                    onValueChangeFinished = { finished++ },
                )
                Slider(0f, { value = it }, "Locked balance", enabled = false, onValueChangeFinished = { finished++ })
            }
        }
        val balance = rule.onNodeWithContentDescription("Balance")
        balance.performSemanticsAction(SemanticsActions.SetProgress) { it(-6.4f) }
        assertEquals(-5f, value, 0.001f)
        assertEquals(1, finished)
        rule.onNodeWithContentDescription("Locked balance").assertIsNotEnabled()
            .assert(SemanticsMatcher.keyNotDefined(SemanticsActions.SetProgress))
        assertEquals(-5f, value, 0.001f)
        assertEquals(1, finished)

        balance.performSemanticsAction(SemanticsActions.RequestFocus)
        balance.assertIsFocused().performKeyInput { pressKey(Key.DirectionRight) }
        assertEquals(0f, value, 0.001f)
        assertEquals(2, finished)
    }

    @Test
    fun sliderUsesEntireRequestedHeight() {
        var value by mutableFloatStateOf(0f)
        var finished = 0
        rule.setThemedContent(Material3Theme, dark) {
            Slider(
                value, { value = it }, "Tall timeline", Modifier.height(80.dp),
                onValueChangeFinished = { finished++ },
            )
        }

        val slider = rule.onNodeWithContentDescription("Tall timeline")
        slider.performTouchInput {
            down(Offset(width - 2f, height - 2f))
            up()
        }
        assertTrue("Bottom-edge tap reaches end of the timeline", value >= 0.95f)
        slider.performTouchInput {
            down(Offset(width - 2f, height - 2f))
            moveTo(Offset(2f, height - 2f))
            up()
        }
        assertTrue("Bottom-edge drag reaches start of the timeline", value <= 0.05f)
        assertEquals(2, finished)
    }

    @Test
    fun cancellingMultipleHoldsReleasesBothControls() {
        val held = mutableSetOf<String>()
        val releases = mutableListOf<String>()
        fun track(label: String, down: Boolean) {
            if (down) held += label else if (held.remove(label)) releases += label
        }
        rule.setThemedContent(Material3Theme, dark) {
            Column {
                DirectionPad({ direction, down -> track(direction.label, down) }, directions = setOf(Direction.Left))
                HoldButton("Boost", { track("Boost", it) })
            }
        }
        val left = rule.onNodeWithContentDescription("Left")
        val boost = rule.onNodeWithContentDescription("Boost")
        left.performTouchInput { down(0, center) }
        boost.performTouchInput { down(1, center) }
        assertEquals(setOf("Left", "Boost"), held)
        boost.performTouchInput { cancel() }
        assertTrue(held.isEmpty())
        assertEquals(2, releases.size)
        assertEquals(setOf("Left", "Boost"), releases.toSet())
    }

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun modes() = ThemeCases.modes()
    }
}
