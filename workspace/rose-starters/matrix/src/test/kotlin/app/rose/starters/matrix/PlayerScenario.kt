package app.rose.starters.matrix

import android.graphics.Bitmap
import android.os.Build
import android.os.Looper
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsOff
import androidx.compose.ui.test.assertIsOn
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipe
import app.rose.design.Theme
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.setThemedContent
import app.rose.template.player.PlayerTemplate
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import org.robolectric.shadows.ShadowMediaPlayer
import java.io.File
import java.time.Duration

/** Same Player interaction under each implemented pilot theme. Renderings come from the JVM. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37], qualifiers = "w411dp-h891dp")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class PlayerScenario(private val id: String, private val theme: Theme, private val dark: Boolean) {
    @get:Rule
    val rule = createComposeRule()

    private val timeline get() = rule.onNodeWithContentDescription("Playback timeline")
    private val loop get() = rule.onNodeWithText("Loop passage")

    @Test
    fun mainInteraction() {
        assumeTrue("${theme.id} is a placeholder", theme.implemented)
        ShadowMediaPlayer.setMediaInfoProvider { ShadowMediaPlayer.MediaInfo(24_000, 0) }
        rule.setThemedContent(theme, dark) { PlayerTemplate.App() }
        rule.onNodeWithText("Between Stations").assertIsDisplayed()
        assertEquals(0f..24_000f, timeline.fetchSemanticsNode().config[SemanticsProperties.ProgressBarRangeInfo].range)

        rule.onNodeWithText("Play").performClick()
        advance(1_300)
        assertPositionIn(1_000..2_400)
        rule.onNodeWithText("Pause").performClick()
        val paused = position()
        advance(1_000)
        assertEquals(paused, position())

        timeline.performTouchInput { swipe(Offset(width * 0.25f, centerY), Offset(width * 0.65f, centerY)) }
        assertPositionIn(14_000..17_000)

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
        rule.onNodeWithText("Pause").assertIsDisplayed().performClick()
        captureLoop()

        scrubTo(20_000)
        loop.assertIsOff()
        assertEquals(20_000, position())
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

    private fun captureLoop() {
        val directory = File("build/screenshots").apply { mkdirs() }
        val mode = if (dark) "dark" else "light"
        File(directory, "player-$id-$mode-api${Build.VERSION.SDK_INT}.png").outputStream().use {
            assertTrue(rule.onRoot().captureToImage().asAndroidBitmap().compress(Bitmap.CompressFormat.PNG, 100, it))
        }
    }

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "{0} dark={2}")
        fun cases() = ThemeCases.of(*Pilots.themes.toTypedArray())
    }
}
