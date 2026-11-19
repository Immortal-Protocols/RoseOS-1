package app.rose.template.player

import android.os.Looper
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertIsOff
import androidx.compose.ui.test.assertIsOn
import androidx.compose.ui.test.junit4.StateRestorationTester
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipe
import app.rose.design.BaselineTheme
import app.rose.design.RoseTheme
import app.rose.design.Theme
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.setThemedContent
import app.rose.starters.examples.PaperTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowMediaPlayer
import java.time.Duration

/** UI behavior and the Android media calls, with the engine shadowing playback on the JVM. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37], qualifiers = "w411dp-h891dp")
class PlayerTemplateBehaviorTest(@Suppress("unused") private val id: String, private val theme: Theme, private val dark: Boolean) {
    @get:Rule
    val rule = createComposeRule()

    private lateinit var media: ShadowMediaPlayer
    private val timeline get() = rule.onNodeWithContentDescription("Playback timeline")
    private val loop get() = rule.onNodeWithText("Loop passage")

    @Before
    fun registerClip() {
        ShadowMediaPlayer.setMediaInfoProvider { ShadowMediaPlayer.MediaInfo(24_000, 0) }
        ShadowMediaPlayer.setCreateListener { _, shadow -> media = shadow }
    }

    @Test
    fun mainInteraction() {
        rule.setThemedContent(theme, dark) { PlayerTemplate.App() }
        rule.onNodeWithText("Between Stations").assertIsDisplayed()
        assertEquals(0f..24_000f, timeline.fetchSemanticsNode().config[SemanticsProperties.ProgressBarRangeInfo].range)
        assertEquals(R.raw.between_stations, media.sourceResId)
        rule.onNodeWithText("Set B").assertIsNotEnabled()

        rule.onNodeWithText("Play").performClick()
        advance(1_300)
        assertPositionIn(1_000..2_400)
        assertEquals(ShadowMediaPlayer.State.STARTED, media.state)
        rule.onNodeWithText("Pause").performClick()
        val paused = position()
        advance(1_000)
        assertEquals(paused, position())
        assertEquals(ShadowMediaPlayer.State.PAUSED, media.state)

        timeline.performTouchInput { swipe(Offset(width * 0.25f, centerY), Offset(width * 0.65f, centerY)) }
        advance(100)
        assertPositionIn(14_000..17_000)
        assertEquals(position(), media.currentPositionRaw)

        scrubTo(6_000)
        rule.onNodeWithText("Set A").performClick()
        rule.onNodeWithText("Start 0:06").assertIsDisplayed()
        scrubTo(12_000)
        rule.onNodeWithText("Set B").performClick()
        rule.onNodeWithText("End 0:12").assertIsDisplayed()
        loop.performClick().assertIsOn()
        assertEquals(6_000, position())
        scrubTo(11_800)
        rule.onNodeWithText("Play").performClick()
        advance(800)
        assertPositionIn(6_000..8_000)
        assertTrue("Engine returned to the loop passage", media.currentPositionRaw in 6_000..8_000)
        rule.onNodeWithText("Pause").assertIsDisplayed().performClick()

        scrubTo(20_000)
        loop.assertIsOff()
        advance(100)
        assertEquals(20_000, media.currentPositionRaw)
    }

    @Test
    fun completionCanBeFollowedByAScrubAndPlayback() {
        rule.setThemedContent(theme, dark) { PlayerTemplate.App() }
        scrubTo(23_600)
        rule.onNodeWithText("Play").performClick()
        advance(1_000)
        rule.onNodeWithText("Play").assertIsDisplayed()
        assertEquals(24_000, position())

        scrubTo(17_000)
        rule.onNodeWithText("Play").performClick()
        advance(800)
        assertPositionIn(17_600..18_500)
        assertEquals(ShadowMediaPlayer.State.STARTED, media.state)
    }

    @Test
    fun restoredSessionKeepsPlayheadAndLoopButWaitsForPlay() {
        val restoration = StateRestorationTester(rule)
        restoration.setContent { RoseTheme(theme, dark) { PlayerTemplate.App() } }
        scrubTo(10_000)
        rule.onNodeWithText("Set A").performClick()
        loop.performClick()
        scrubTo(11_000)
        rule.onNodeWithText("Play").performClick()
        advance(400)
        restoration.emulateSavedInstanceStateRestore()
        rule.onNodeWithText("Play").assertIsDisplayed()
        rule.onNodeWithText("Start 0:10").assertIsDisplayed()
        loop.assertIsOn()
        assertPositionIn(11_000..12_500)
    }

    private fun scrubTo(millis: Int) {
        timeline.performSemanticsAction(SemanticsActions.SetProgress) { it(millis.toFloat()) }
    }

    private fun position() = timeline.fetchSemanticsNode().config[SemanticsProperties.ProgressBarRangeInfo].current.toInt()

    private fun assertPositionIn(expected: IntRange) {
        val actual = position()
        assertTrue("Expected $actual in $expected", actual in expected)
    }

    private fun advance(millis: Long) = shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(millis))

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "{0} dark={2}")
        fun cases() = ThemeCases.of(BaselineTheme, PaperTheme)
    }
}
