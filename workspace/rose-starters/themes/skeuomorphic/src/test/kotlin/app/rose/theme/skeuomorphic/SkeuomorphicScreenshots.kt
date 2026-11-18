package app.rose.theme.skeuomorphic

import android.graphics.Bitmap
import android.os.Build
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.unit.dp
import app.rose.design.BarAction
import app.rose.design.Button
import app.rose.design.ButtonKind
import app.rose.design.DirectionPad
import app.rose.design.HoldButton
import app.rose.design.IconButton
import app.rose.design.ListItem
import app.rose.design.Panel
import app.rose.design.PanelKind
import app.rose.design.RoseIcon
import app.rose.design.Screen
import app.rose.design.Segmented
import app.rose.design.Slider
import app.rose.design.Stat
import app.rose.design.Text
import app.rose.design.TextField
import app.rose.design.TextRole
import app.rose.design.Toggle
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.setThemedContent
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File

/** Native-graphics images of the stock controls, rendered by Robolectric. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34, 37], qualifiers = "w411dp-h891dp-xhdpi")
class SkeuomorphicScreenshots(private val dark: Boolean) {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun renderAndCheckHeldKeysReturnToRestingAppearance() {
        rule.setThemedContent(SkeuomorphicTheme, dark) { ControlShowcase() }
        val left = rule.onNodeWithContentDescription("Left")
        val jump = rule.onNodeWithContentDescription("Jump")
        val resting = rule.onRoot().captureToImage().asAndroidBitmap()
        left.performTouchInput { down(0, center) }
        jump.performTouchInput { down(1, center) }
        val held = rule.onRoot().captureToImage().asAndroidBitmap()
        assertFalse("holding keys changes their appearance", resting.sameAs(held))
        jump.performTouchInput { up(1) }
        left.performTouchInput { up(0) }
        assertTrue("releasing both keys restores their appearance", resting.sameAs(rule.onRoot().captureToImage().asAndroidBitmap()))
        save(resting, "resting")
        save(held, "held")
    }

    private fun save(bitmap: Bitmap, state: String) {
        val mode = if (dark) "dark" else "light"
        val output = File("build/screenshots").apply { mkdirs() }
        File(output, "skeuomorphic-api${Build.VERSION.SDK_INT}-$mode-$state.png").outputStream().use {
            assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it))
        }
    }

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun modes() = ThemeCases.modes()
    }
}

@Composable
private fun ControlShowcase() {
    Screen("Sound bench", actions = listOf(BarAction(RoseIcon.Tune, "Settings", {}))) { padding ->
        Column(
            Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Panel(Modifier.fillMaxWidth(), kind = PanelKind.Raised) {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Playback", role = TextRole.Title)
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                        IconButton(RoseIcon.SkipBack, "Previous", {})
                        IconButton(RoseIcon.Play, "Play", {}, prominent = true)
                        IconButton(RoseIcon.SkipForward, "Next", {})
                        IconButton(RoseIcon.Loop, "Loop", {}, checked = true)
                    }
                    var position by remember { mutableFloatStateOf(0.42f) }
                    Slider(position, { position = it }, "Position", highlight = 0.26f..0.66f, marks = listOf(0.26f, 0.66f))
                    Text("1:21 / 3:12", role = TextRole.Label)
                    Segmented(listOf("0.5×", "1×", "2×"), 1, {}, "Speed")
                    Toggle(true, {}, "Repeat section")
                }
            }
            ListItem(
                "Field recording", supporting = "Stereo · 3:12", leadingIcon = RoseIcon.Image, selected = true, onClick = {},
                trailing = { IconButton(RoseIcon.More, "Recording actions", {}) },
            )
            Panel(Modifier.fillMaxWidth(), kind = PanelKind.Inset) {
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    DirectionPad({ _, _ -> })
                    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Stat("Charge", "68%", progress = 0.68f)
                        HoldButton("Jump", {}, icon = RoseIcon.Jump)
                    }
                }
            }
            TextField("", {}, "Marker name", placeholder = "Name this position")
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button("Save", {}, kind = ButtonKind.Primary)
                Button("Clear", {}, kind = ButtonKind.Danger, enabled = false)
            }
        }
    }
}
