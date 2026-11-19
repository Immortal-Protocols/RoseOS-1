package app.rose.starters.matrix

import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasScrollAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performSemanticsAction
import app.rose.design.Theme
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.setThemedContent
import app.rose.template.feed.FeedTemplate
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config

/** Drives the same Feed interaction through every implemented pilot theme. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37], qualifiers = "w411dp-h891dp")
class FeedScenario(@Suppress("unused") private val id: String, private val theme: Theme, private val dark: Boolean) {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun scrollReadSaveAndReturn() {
        assumeTrue("${theme.id} is a placeholder", theme.implemented)
        rule.setThemedContent(theme, dark) { FeedTemplate.App() }
        val target = hasText("The long way home") and hasClickAction()
        val feed = rule.onNode(hasScrollAction())
        feed.performScrollToNode(target)
        val card = rule.onNode(target).assertIsDisplayed()
        val alignedY = card.fetchSemanticsNode().positionInRoot.y
        feed.performSemanticsAction(SemanticsActions.ScrollBy) { it(0f, 35f) }
        card.assertIsDisplayed()
        val originalY = card.fetchSemanticsNode().positionInRoot.y
        assertTrue(originalY < alignedY)

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
        rule.onNodeWithText("Remove from saved").assertIsDisplayed()
    }

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "{0} dark={2}")
        fun cases() = ThemeCases.of(*Pilots.themes.toTypedArray())
    }
}
