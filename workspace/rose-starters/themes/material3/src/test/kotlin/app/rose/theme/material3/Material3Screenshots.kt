package app.rose.theme.material3

import android.graphics.Bitmap
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
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
import app.rose.design.BarAction
import app.rose.design.Button
import app.rose.design.ButtonKind
import app.rose.design.Direction
import app.rose.design.DirectionPad
import app.rose.design.HoldButton
import app.rose.design.IconButton
import app.rose.design.ListItem
import app.rose.design.Panel
import app.rose.design.PanelKind
import app.rose.design.RoseIcon
import app.rose.design.RoseTheme
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
import java.io.File
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/** Visual evidence from Robolectric native graphics. Each mode includes a live hold press. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [37], qualifiers = "w411dp-h891dp-xxhdpi")
class Material3Screenshots(private val dark: Boolean) {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun renderControlsAndHoldState() {
        rule.setThemedContent(Material3Theme, dark) { ControlSheet() }
        save("controls")
        val hold = rule.onNodeWithContentDescription("Jump")
        hold.performTouchInput { down(center) }
        save("held")
        hold.performTouchInput { up() }
    }

    private fun save(state: String) {
        val mode = if (dark) "dark" else "light"
        val out = File("build/screenshots").apply { mkdirs() }
        File(out, "material3-$mode-$state.png").outputStream().use {
            check(rule.onRoot().captureToImage().asAndroidBitmap().compress(Bitmap.CompressFormat.PNG, 100, it))
        }
    }

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun modes() = ThemeCases.modes()
    }
}

@Composable
private fun ControlSheet() {
    val spacing = RoseTheme.spacing
    var position by remember { mutableFloatStateOf(0.40f) }
    var looping by remember { mutableStateOf(true) }
    var selected by remember { mutableIntStateOf(1) }
    var name by remember { mutableStateOf("Morning set") }
    Screen(
        "Material 3 controls",
        actions = listOf(BarAction(RoseIcon.Loop, "Loop", { looping = !looping }, checked = looping)),
    ) { insets ->
        Column(
            Modifier.fillMaxSize().padding(insets).padding(spacing.l),
            verticalArrangement = Arrangement.spacedBy(spacing.m),
        ) {
            Panel(Modifier.fillMaxWidth(), kind = PanelKind.Raised) {
                Column(verticalArrangement = Arrangement.spacedBy(spacing.s)) {
                    Text("Playback", role = TextRole.Title)
                    Slider(position, { position = it }, "Timeline", highlight = 0.25f..0.65f, marks = listOf(0.15f, 0.72f, 0.9f))
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(spacing.m),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        IconButton(RoseIcon.Play, "Play", {}, prominent = true)
                        Segmented(listOf("0.5×", "1×", "2×"), selected, { selected = it }, "Speed")
                    }
                    Toggle(looping, { looping = it }, "Loop passage")
                }
            }
            ListItem(
                "Chapter 02", supporting = "1:20 – 3:14", selected = true, leadingIcon = RoseIcon.Play,
                trailing = { IconButton(RoseIcon.Like, "Favorite", {}, checked = true) },
            )
            Panel(Modifier.fillMaxWidth(), kind = PanelKind.Inset) {
                Column(verticalArrangement = Arrangement.spacedBy(spacing.m)) {
                    Stat("Energy", "75%", progress = 0.75f)
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(spacing.l),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        DirectionPad({ _, _ -> }, directions = setOf(Direction.Left, Direction.Right))
                        HoldButton("Jump", {}, icon = RoseIcon.Jump)
                    }
                }
            }
            TextField(name, { name = it }, "Preset name", Modifier.fillMaxWidth())
            Row(horizontalArrangement = Arrangement.spacedBy(spacing.s)) {
                Button("Save", {}, kind = ButtonKind.Primary)
                Button("Share", {}, enabled = false)
                Button("Delete", {}, kind = ButtonKind.Danger)
            }
        }
    }
}
