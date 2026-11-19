package app.rose.theme.skeuomorphic

import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.performKeyInput
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.pressKey
import androidx.compose.ui.unit.LayoutDirection
import app.rose.design.Slider
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.setThemedContent
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37])
class SkeuomorphicSliderTest(private val dark: Boolean) {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun rightToLeftTouchAndKeyboardSnapAndFinish() {
        val focus = FocusRequester()
        var value by mutableFloatStateOf(0.5f)
        var finished = 0
        rule.setThemedContent(SkeuomorphicTheme, dark) {
            CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl) {
                Slider(
                    value, { value = it }, "Position", Modifier.focusRequester(focus), steps = 3,
                    onValueChangeFinished = { finished++ }, highlight = 0.25f..0.75f, marks = listOf(0.25f, 0.75f),
                )
            }
        }
        val track = rule.onNodeWithContentDescription("Position")
        track.performTouchInput { down(centerRight.copy(x = width - 1f)); up() }
        assertEquals(0f, value, 0.001f)
        assertEquals(1, finished)
        rule.runOnIdle { focus.requestFocus() }
        track.performKeyInput { pressKey(Key.DirectionLeft) }
        assertEquals(0.25f, value, 0.001f)
        assertEquals(2, finished)
        track.performKeyInput { pressKey(Key.MoveEnd) }
        assertEquals(1f, value, 0.001f)
        assertEquals(3, finished)
    }

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun modes() = ThemeCases.modes()
    }
}
