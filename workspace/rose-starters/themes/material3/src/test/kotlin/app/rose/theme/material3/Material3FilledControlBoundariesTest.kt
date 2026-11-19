package app.rose.theme.material3

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.toPixelMap
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.unit.dp
import app.rose.design.Button
import app.rose.design.Direction
import app.rose.design.DirectionPad
import app.rose.design.HoldButton
import app.rose.design.IconButton
import app.rose.design.Panel
import app.rose.design.PanelKind
import app.rose.design.RoseIcon
import app.rose.design.RoseTheme
import app.rose.design.testing.Contrast
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.setThemedContent
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34, 37], qualifiers = "w411dp-h891dp-xxhdpi")
class Material3FilledControlBoundariesTest(private val dark: Boolean) {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun filledControlsDrawVisibleBoundariesAcrossSurfaces() {
        val surfaces = listOf("Background", "Raised", "Flat", "Inset", "Surface variant")
        rule.setThemedContent(Material3Theme, dark) {
            Column {
                surfaces.forEach { surface ->
                    val body: @Composable () -> Unit = {
                        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Button("Secondary", {}, Modifier.fillMaxWidth().testTag("$surface-secondary"))
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                IconButton(RoseIcon.Loop, "Loop", {}, Modifier.testTag("$surface-tonal"), checked = false)
                                IconButton(
                                    RoseIcon.Play, "Play", {}, Modifier.testTag("$surface-prominent"),
                                    checked = false, prominent = true,
                                )
                                DirectionPad(
                                    { _, _ -> }, Modifier.testTag("$surface-direction"), directions = setOf(Direction.Left),
                                )
                                HoldButton("Jump", {}, Modifier.testTag("$surface-hold"))
                            }
                        }
                    }
                    val modifier = Modifier.fillMaxWidth().testTag(surface)
                    when (surface) {
                        "Raised" -> Panel(modifier, kind = PanelKind.Raised, content = body)
                        "Flat" -> Panel(modifier, kind = PanelKind.Flat, content = body)
                        "Inset" -> Panel(modifier, kind = PanelKind.Inset, content = body)
                        else -> {
                            val background = if (surface == "Background") RoseTheme.colors.background
                                else RoseTheme.colors.surfaceVariant
                            Column(modifier.background(background).padding(RoseTheme.spacing.l)) { body() }
                        }
                    }
                }
            }
        }

        val horizontalPadding = with(rule.density) { Material3Theme.tokens(dark).spacing.l.roundToPx() }
        val edgePixel = with(rule.density) { 0.5.dp.roundToPx() }
        for (surface in surfaces) {
            val panel = rule.onNodeWithTag(surface).captureToImage().toPixelMap()
            val background = panel[horizontalPadding / 2, panel.height / 2]
            val button = rule.onNodeWithTag("$surface-secondary").captureToImage().toPixelMap()
            assertEquals(panel.width - 2 * horizontalPadding, button.width)
            Contrast.require("$surface full-width button left edge", button[edgePixel, button.height / 2], background, 3.0)
            Contrast.require(
                "$surface full-width button right edge", button[button.width - 1 - edgePixel, button.height / 2], background, 3.0,
            )

            for (control in listOf("tonal", "prominent", "direction", "hold")) {
                val image = rule.onNodeWithTag("$surface-$control").captureToImage().toPixelMap()
                Contrast.require("$surface $control top edge", image[image.width / 2, edgePixel], background, 3.0)
            }
        }
    }

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun modes() = ThemeCases.modes()
    }
}
