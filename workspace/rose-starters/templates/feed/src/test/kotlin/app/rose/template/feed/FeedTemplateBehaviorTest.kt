package app.rose.template.feed

import androidx.activity.ComponentActivity
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.SemanticsNodeInteraction
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasScrollAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.StateRestorationTester
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performSemanticsAction
import app.rose.design.BaselineTheme
import app.rose.design.RoseTheme
import app.rose.design.Theme
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.setThemedContent
import app.rose.starters.examples.PaperTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37], qualifiers = "w411dp-h891dp")
class FeedTemplateBehaviorTest(@Suppress("unused") private val id: String, private val theme: Theme, private val dark: Boolean) {
    @get:Rule
    val rule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun scrollReadSaveAndReturn() {
        rule.setThemedContent(theme, dark) { FeedTemplate.App() }
        val card = scrollToPost()
        val originalY = card.fetchSemanticsNode().positionInRoot.y

        card.performClick()
        rule.onNodeWithText("The long way home").assertIsDisplayed()
        rule.onNodeWithText("Take the footbridge behind the station", substring = true).assertExists()
        rule.onNodeWithText("Save post").performClick()
        rule.onNodeWithText("Remove from saved").assertIsDisplayed()
        rule.onNodeWithContentDescription("Back to feed").performClick()

        card.assertIsDisplayed()
        assertEquals(originalY, card.fetchSemanticsNode().positionInRoot.y, 0f)
        rule.onNodeWithContentDescription("Saved", useUnmergedTree = true).assertExists()
        card.performClick()
        rule.onNodeWithText("Remove from saved").performClick()
        rule.onNodeWithText("Save post").assertIsDisplayed()
        rule.runOnIdle { rule.activity.onBackPressedDispatcher.onBackPressed() }

        card.assertIsDisplayed()
        assertEquals(originalY, card.fetchSemanticsNode().positionInRoot.y, 0f)
        rule.onNodeWithContentDescription("Saved", useUnmergedTree = true).assertDoesNotExist()
    }

    @Test
    fun restoresOpenPostSavedStateAndFeedOffset() {
        val restoration = StateRestorationTester(rule)
        restoration.setContent { RoseTheme(theme, dark) { FeedTemplate.App() } }
        val card = scrollToPost()
        val originalY = card.fetchSemanticsNode().positionInRoot.y
        card.performClick()
        rule.onNodeWithText("Save post").performClick()

        restoration.emulateSavedInstanceStateRestore()

        rule.onNodeWithText("The long way home").assertIsDisplayed()
        rule.onNodeWithText("Remove from saved").assertIsDisplayed()
        rule.onNodeWithContentDescription("Back to feed").performClick()
        card.assertIsDisplayed()
        assertEquals(originalY, card.fetchSemanticsNode().positionInRoot.y, 0f)
    }

    private fun scrollToPost(): SemanticsNodeInteraction {
        val target = hasText("The long way home") and hasClickAction()
        val feed = rule.onNode(hasScrollAction())
        feed.performScrollToNode(target)
        val card = rule.onNode(target).assertIsDisplayed()
        val alignedY = card.fetchSemanticsNode().positionInRoot.y
        // Add a pixel offset so restoring just the list index cannot pass.
        feed.performSemanticsAction(SemanticsActions.ScrollBy) { it(0f, 35f) }
        assertTrue(card.fetchSemanticsNode().positionInRoot.y < alignedY)
        return card.assertIsDisplayed()
    }

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "{0} dark={2}")
        fun cases() = ThemeCases.of(BaselineTheme, PaperTheme)
    }
}
