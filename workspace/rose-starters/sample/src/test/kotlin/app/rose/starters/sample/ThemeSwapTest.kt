package app.rose.starters.sample

import app.rose.starters.examples.PaperTheme
import app.rose.starters.examples.TallyTemplate
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import app.rose.design.BaselineTheme
import app.rose.design.RoseTheme
import app.rose.design.RoseTokens
import app.rose.design.Theme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The swap itself. MainActivity's one-line change is a source edit; this does the same swap at runtime, mid-session,
 * around the unchanged `TallyTemplate.App()`, and checks the template keeps its own state and keeps working, including
 * across a theme that wraps the app in its own Provide (as the Material 3 theme will).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34, 37], qualifiers = "w411dp-h891dp")
class ThemeSwapTest {
    @get:Rule
    val rule = createComposeRule()

    /** Paper's look, plus a Provide wrapper, like a theme built on another design system. */
    private object WrappingPaper : Theme by PaperTheme {
        override val id = "wrapping-paper"
        @Composable
        override fun Provide(tokens: RoseTokens, content: @Composable () -> Unit) = androidx.compose.foundation.layout.Box { content() }
    }

    @Test
    fun swappingTheThemeKeepsTheTemplatesStateAndBehaviour() {
        var theme by mutableStateOf<Theme>(BaselineTheme)
        var seen = ""
        rule.setContent {
            RoseTheme(theme, dark = false) {
                seen = RoseTheme.id
                TallyTemplate.App(Modifier)
            }
        }
        rule.onNodeWithContentDescription("Increase Apples").performClick().performClick()
        rule.onNodeWithText("2 of 10").assertExists()

        for (next in listOf(PaperTheme, WrappingPaper, BaselineTheme)) {
            theme = next
            rule.waitForIdle()
            assertEquals(next.id, seen)
            rule.onNodeWithText("2 of 10").assertExists() // the template's state survived the swap
        }
        rule.onNodeWithContentDescription("Increase Apples").performClick()
        rule.onNodeWithText("3 of 10").assertExists() // and it still behaves the same
    }
}
