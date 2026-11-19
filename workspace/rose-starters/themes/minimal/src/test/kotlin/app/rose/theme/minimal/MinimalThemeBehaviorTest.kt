package app.rose.theme.minimal

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.assertHeightIsAtLeast
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performKeyInput
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.pressKey
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import app.rose.design.Slider
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.setThemedContent
import app.rose.starters.examples.TallyTemplate
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37], qualifiers = "w411dp-h891dp")
class MinimalThemeBehaviorTest(private val dark: Boolean) {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun tallyKeepsStepCountAndProgressAcrossNavigation() {
        rule.setThemedContent(MinimalTheme, dark) { TallyTemplate.App() }
        rule.onNodeWithText("+2").performClick().assertIsSelected()
        val increment = rule.onNodeWithContentDescription("Increase Apples")
        repeat(2) { increment.performClick() }
        rule.onNodeWithText("4 of 10").assertExists()
        assertApplesProgress(0.4f)

        rule.onNodeWithText("Apples").performClick()
        rule.onNodeWithText("6 to go").assertExists()
        rule.onNodeWithText("Add 2").performClick()
        assertApplesProgress(0.6f)

        rule.onNodeWithContentDescription("Back").performClick()
        rule.onNodeWithText("+2").assertIsSelected()
        rule.onNodeWithText("6 of 10").assertExists()
        assertApplesProgress(0.6f)
    }

    @Test
    fun timelineTapsMirrorInRightToLeftLayout() {
        var value by mutableFloatStateOf(30f)
        var finishes = 0
        rule.setThemedContent(MinimalTheme, dark) {
            CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl) {
                Column(Modifier.padding(horizontal = 24.dp)) {
                    Slider(
                        value, { value = it }, "Position", Modifier.width(320.dp),
                        valueRange = 10f..50f, steps = 3,
                        onValueChangeFinished = { finishes++ }, highlight = 20f..40f, marks = listOf(20f, 40f),
                    )
                }
            }
        }
        val slider = rule.onNodeWithContentDescription("Position").assertHeightIsAtLeast(48.dp)
        slider.performTouchInput { down(topRight.copy(x = width - 4f, y = 4f)); up() }
        rule.runOnIdle { assertEquals(10f, value, 0f) }
        slider.performTouchInput { down(center); up() }
        rule.runOnIdle { assertEquals(30f, value, 0f) }
        slider.performTouchInput { down(bottomLeft.copy(x = 4f, y = height - 4f)); up() }
        rule.runOnIdle {
            assertEquals(50f, value, 0f)
            assertEquals(3, finishes)
        }
    }

    @Test
    fun focusedTimelineStepsAndJumpsToEndpointsFromKeyboard() {
        var value by mutableFloatStateOf(30f)
        var finishes = 0
        rule.setThemedContent(MinimalTheme, dark) {
            Slider(
                value, { value = it }, "Position", valueRange = 10f..50f, steps = 3,
                onValueChangeFinished = { finishes++ },
            )
        }
        val slider = rule.onNodeWithContentDescription("Position")
        slider.performSemanticsAction(SemanticsActions.RequestFocus)
        slider.performKeyInput { pressKey(Key.DirectionRight) }
        assertEquals(40f, value, 0f)
        slider.performKeyInput { pressKey(Key.MoveEnd) }
        assertEquals(50f, value, 0f)
        slider.performKeyInput { pressKey(Key.MoveHome) }
        assertEquals(10f, value, 0f)
        assertEquals(3, finishes)
    }

    private fun assertApplesProgress(expected: Float) {
        val progress = rule.onNodeWithContentDescription("Apples progress").fetchSemanticsNode()
        assertEquals(expected, progress.config[SemanticsProperties.ProgressBarRangeInfo].current, 0.001f)
    }

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun modes() = ThemeCases.modes()
    }
}
