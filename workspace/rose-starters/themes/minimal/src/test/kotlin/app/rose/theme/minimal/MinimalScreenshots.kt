package app.rose.theme.minimal

import android.graphics.Bitmap
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import app.rose.design.Button
import app.rose.design.ButtonKind
import app.rose.design.Direction
import app.rose.design.DirectionPad
import app.rose.design.HoldButton
import app.rose.design.IconButton
import app.rose.design.ListItem
import app.rose.design.Panel
import app.rose.design.PanelKind
import app.rose.design.Progress
import app.rose.design.RoseIcon
import app.rose.design.RoseTheme
import app.rose.design.Screen
import app.rose.design.Segmented
import app.rose.design.Slider
import app.rose.design.Stat
import app.rose.design.Text
import app.rose.design.TextRole
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.setThemedContent
import app.rose.starters.examples.TallyTemplate
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File

/** Native-graphics Robolectric renders for visual review of the stock controls and the Home example. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34, 37], qualifiers = "w411dp-h891dp-xxhdpi")
class MinimalScreenshots(private val dark: Boolean) {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun renderTally() {
        rule.setThemedContent(MinimalTheme, dark) { TallyTemplate.App() }
        repeat(3) { rule.onNodeWithContentDescription("Increase Apples").performClick() }
        capture("tally")
    }

    @Test
    fun renderControls() {
        rule.setThemedContent(MinimalTheme, dark) {
            val s = RoseTheme.spacing
            Screen("Minimal controls") { insets ->
                Column(
                    Modifier.verticalScroll(rememberScrollState()).padding(insets).padding(s.l),
                    verticalArrangement = Arrangement.spacedBy(s.l),
                ) {
                    Segmented(listOf("Today", "Week", "Month"), 1, {}, "Range")
                    ListItem("Field recording", supporting = "Chapter 2", selected = true, leadingIcon = RoseIcon.Play, onClick = {})
                    Panel(kind = PanelKind.Inset) {
                        Column(verticalArrangement = Arrangement.spacedBy(s.s)) {
                            Text("1:32 / 3:40", role = TextRole.Title)
                            Slider(92f, {}, "Position", valueRange = 0f..220f, highlight = 70f..130f, marks = listOf(70f, 130f, 180f))
                            Row(horizontalArrangement = Arrangement.spacedBy(s.s)) {
                                IconButton(RoseIcon.Play, "Play", {}, prominent = true)
                                IconButton(RoseIcon.Loop, "Loop", {}, checked = true)
                            }
                        }
                    }
                    Progress(0.6f, "Download")
                    Row(horizontalArrangement = Arrangement.spacedBy(s.xl)) {
                        Stat("Score", "1200")
                        Stat("Health", "3 / 5", progress = 0.6f)
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(s.m)) {
                        DirectionPad({ _, _ -> }, directions = setOf(Direction.Left, Direction.Right))
                        HoldButton("Jump", {}, icon = RoseIcon.Jump)
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(s.s)) {
                        Button("Continue", {}, kind = ButtonKind.Primary)
                        Button("Reset", {}, kind = ButtonKind.Danger)
                    }
                }
            }
        }
        capture("controls")
    }

    private fun capture(screen: String) {
        val mode = if (dark) "dark" else "light"
        val directory = File("build/screenshots").apply { mkdirs() }
        val file = File(directory, "minimal-$screen-api${RuntimeEnvironment.getApiLevel()}-$mode.png")
        file.outputStream().use {
            assertTrue(rule.onRoot().captureToImage().asAndroidBitmap().compress(Bitmap.CompressFormat.PNG, 100, it))
        }
    }

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun modes() = ThemeCases.modes()
    }
}
