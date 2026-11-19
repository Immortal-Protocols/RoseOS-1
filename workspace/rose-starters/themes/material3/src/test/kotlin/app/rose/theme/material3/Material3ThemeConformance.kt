package app.rose.theme.material3

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

/** OWNER: Material 3 theme engineer. The Controls contract under Material 3, light and dark, API 34 and 37. Must pass unchanged. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37])
class Material3ThemeConformance(dark: Boolean) : ThemeConformanceTest(Material3Theme, dark) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun modes() = ThemeCases.modes()
    }
}

/** A real template under Material 3, before the pilot templates exist: the example Tally still counts. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34, 37], qualifiers = "w411dp-h891dp")
class Material3ThemeWithTallyTest {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun tallyStillCounts() {
        assumeTrue("Material 3 theme is a placeholder", Material3Theme.implemented)
        rule.setThemedContent(Material3Theme, dark = false) { TallyTemplate.App() }
        rule.onNodeWithContentDescription("Increase Apples").performClick().performClick()
        rule.onNodeWithText("2 of 10").assertExists()
    }
}
