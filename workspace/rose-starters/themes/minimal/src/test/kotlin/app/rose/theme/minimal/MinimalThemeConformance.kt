package app.rose.theme.minimal

import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.ThemeConformanceTest
import app.rose.design.testing.setThemedContent
import app.rose.starters.examples.TallyTemplate
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** OWNER: Minimal theme engineer. The Controls contract under Minimal, light and dark, API 34 and 37. Must pass unchanged. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37])
class MinimalThemeConformance(dark: Boolean) : ThemeConformanceTest(MinimalTheme, dark) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun modes() = ThemeCases.modes()
    }
}

/** A real template under Minimal, before the pilot templates exist: the example Tally still counts. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34, 37], qualifiers = "w411dp-h891dp")
class MinimalThemeWithTallyTest {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun tallyStillCounts() {
        assumeTrue("Minimal theme is a placeholder", MinimalTheme.implemented)
        rule.setThemedContent(MinimalTheme, dark = false) { TallyTemplate.App() }
        rule.onNodeWithContentDescription("Increase Apples").performClick().performClick()
        rule.onNodeWithText("2 of 10").assertExists()
    }
}
