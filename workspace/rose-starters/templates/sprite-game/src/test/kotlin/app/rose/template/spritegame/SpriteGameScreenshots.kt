package app.rose.template.spritegame

import android.graphics.Bitmap
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import app.rose.design.BaselineTheme
import app.rose.design.Theme
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.setThemedContent
import app.rose.starters.examples.PaperTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File

/** JVM renderings of the opening board and spike collision under both reference themes. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [37], qualifiers = "w411dp-h891dp-xxhdpi")
class SpriteGameScreenshots(private val id: String, private val theme: Theme, private val dark: Boolean) {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun renderRound() {
        rule.setThemedContent(theme, dark) { SpriteGameTemplate.App() }
        capture("start")
        repeat(4) { rule.onNodeWithContentDescription("Down").performClick() }
        repeat(3) { rule.onNodeWithContentDescription("Right").performClick() }
        rule.onNodeWithText("Signal lost").assertExists()
        capture("collision")
    }

    private fun capture(state: String) {
        val directory = File("build/screenshots").apply { mkdirs() }
        val mode = if (dark) "dark" else "light"
        File(directory, "sprite-game-$id-$mode-$state.png").outputStream().use {
            check(rule.onRoot().captureToImage().asAndroidBitmap().compress(Bitmap.CompressFormat.PNG, 100, it))
        }
    }

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "{0} dark={2}")
        fun cases() = ThemeCases.of(BaselineTheme, PaperTheme)
    }
}
