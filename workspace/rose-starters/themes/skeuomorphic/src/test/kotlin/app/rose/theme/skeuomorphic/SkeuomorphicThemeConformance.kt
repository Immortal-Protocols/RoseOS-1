package app.rose.theme.skeuomorphic

import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.click
import androidx.compose.ui.test.performTouchInput
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.ThemeConformanceTest
import app.rose.design.testing.setThemedContent
import app.rose.starters.examples.TallyTemplate
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config

/** OWNER: Skeuomorphic theme engineer. The Controls contract under Skeuomorphic, light and dark, API 34 and 37. Must pass unchanged. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37])
class SkeuomorphicThemeConformance(dark: Boolean) : ThemeConformanceTest(SkeuomorphicTheme, dark) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun modes() = ThemeCases.modes()
    }
}

/** The same unmodified example exercises the theme through its public stock controls. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37], qualifiers = "w411dp-h891dp")
class SkeuomorphicThemeWithTallyTest(private val dark: Boolean) {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun tallySelectsCountsAndReturnsToTheSameSession() {
        rule.setThemedContent(SkeuomorphicTheme, dark) { TallyTemplate.App() }
        rule.onNodeWithText("+2").performTouchInput { click() }
        rule.onNodeWithContentDescription("Increase Apples").performTouchInput { click() }
        rule.onNodeWithText("2 of 10").assertExists()
        rule.onNodeWithText("Apples").performTouchInput { click() }
        rule.onNodeWithText("Add 2").performTouchInput { click() }
        rule.onNodeWithContentDescription("Back").performTouchInput { click() }
        rule.onNodeWithText("4 of 10").assertExists()
    }

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun modes() = ThemeCases.modes()
    }
}
