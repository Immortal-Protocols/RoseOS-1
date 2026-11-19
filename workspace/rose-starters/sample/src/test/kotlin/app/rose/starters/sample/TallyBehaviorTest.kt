package app.rose.starters.sample

import app.rose.starters.examples.PaperTheme
import app.rose.starters.examples.TallyState
import app.rose.starters.examples.TallyScreen
import android.os.Build
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.hasScrollAction
import app.rose.design.BaselineTheme
import app.rose.design.Theme
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.setThemedContent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * One behaviour script, written once against the template, run unchanged under every theme in both modes on API 34
 * and API 37. It drives Tally only through what a person sees and does (labels, descriptions, taps, drags, typing),
 * and checks the template's own state, so a theme that changed behaviour, not just looks, fails here.
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37], qualifiers = "w411dp-h891dp")
class TallyBehaviorTest(@Suppress("unused") private val id: String, private val theme: Theme, private val dark: Boolean) {

    @get:Rule
    val rule = createComposeRule()

    @Test
    fun sameBehaviourUnderEveryTheme() {
        assertTrue("runs on API 34 or 37, was ${Build.VERSION.SDK_INT}", Build.VERSION.SDK_INT == 34 || Build.VERSION.SDK_INT == 37)
        val state = TallyState.sample()
        rule.setThemedContent(theme, dark) { TallyScreen(state) }
        val list = rule.onNode(hasScrollAction())
        list.performScrollToNode(hasContentDescription("Decrease Pears"))

        // Count up by one, then by five.
        rule.onNodeWithContentDescription("Increase Apples").performClick().performClick()
        assertEquals(2, state.counters[0].count)
        list.performScrollToNode(hasText("+5"))
        rule.onNodeWithText("+5").performClick()
        list.performScrollToNode(hasContentDescription("Decrease Pears"))
        rule.onNodeWithContentDescription("Increase Apples").performClick()
        assertEquals(7, state.counters[0].count)

        // Below zero only when allowed.
        rule.onNodeWithContentDescription("Decrease Pears").performClick()
        assertEquals(0, state.counters[1].count)
        list.performScrollToNode(hasText("Allow counts below zero"))
        rule.onNodeWithText("Allow counts below zero").performClick()
        list.performScrollToNode(hasContentDescription("Decrease Pears"))
        rule.onNodeWithContentDescription("Decrease Pears").performClick()
        assertEquals(-5, state.counters[1].count)

        // The goal slider: stepped 5..50.
        rule.onNodeWithContentDescription("Goal").performSemanticsAction(SemanticsActions.SetProgress) { it(21f) }
        assertEquals(20, state.goal)
        list.performScrollToNode(hasText("7 of 20"))

        // Add a counter by typing.
        list.performScrollToNode(hasSetTextAction())
        rule.onNode(hasSetTextAction()).performTextInput("Plums")
        rule.onNodeWithText("Add").performClick()
        assertEquals(listOf("Apples", "Pears", "Glasses of water", "Plums"), state.counters.map { it.name })

        // Open one, change it there, come back.
        list.performScrollToNode(hasText("Apples"))
        rule.onNodeWithText("Apples").performClick()
        assertEquals(1, state.openId)
        rule.onNodeWithText("Add 5").performClick()
        assertEquals(12, state.counters[0].count)
        rule.onNodeWithContentDescription("Back").performClick()
        assertEquals(null, state.openId)

        // Reset everything.
        rule.onNodeWithContentDescription("Reset all").performClick()
        assertEquals(listOf(0, 0, 0, 0), state.counters.map { it.count })
    }

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "{0} dark={2}")
        fun cases() = ThemeCases.of(BaselineTheme, PaperTheme)
    }
}
