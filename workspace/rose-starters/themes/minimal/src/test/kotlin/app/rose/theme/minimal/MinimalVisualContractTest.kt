package app.rose.theme.minimal

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.graphics.toPixelMap
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.assertHeightIsEqualTo
import androidx.compose.ui.test.assertWidthIsEqualTo
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.unit.dp
import app.rose.design.Button
import app.rose.design.ButtonKind
import app.rose.design.IconButton
import app.rose.design.Panel
import app.rose.design.PanelKind
import app.rose.design.RoseIcon
import app.rose.design.Segmented
import app.rose.design.Text
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
class MinimalVisualContractTest(private val dark: Boolean) {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun filledButtonAndTabStripDrawAtCallerSize() {
        rule.setThemedContent(MinimalTheme, dark) {
            Column(Modifier.width(320.dp)) {
                Button(
                    "Continue", {}, Modifier.fillMaxWidth().height(64.dp).testTag("primary"),
                    kind = ButtonKind.Primary,
                )
                Segmented(listOf("Day", "Week"), 0, {}, "Range", Modifier.fillMaxWidth().height(80.dp))
            }
        }

        val colors = MinimalTheme.tokens(dark).colors
        rule.onNodeWithTag("primary").assertWidthIsEqualTo(320.dp).assertHeightIsEqualTo(64.dp)
        assertLeftEdge("primary", colors.onBackground)
        val selectedTab = rule.onNodeWithText("Day").assertWidthIsEqualTo(160.dp).assertHeightIsEqualTo(80.dp)
        val tabPixels = selectedTab.captureToImage().toPixelMap()
        assertEquals(colors.accent.toArgb(), tabPixels[tabPixels.width / 2, tabPixels.height - 2].toArgb())
    }

    @Test
    fun filledControlsKeepAnOutlineOnMatchingSurfaces() {
        rule.setThemedContent(MinimalTheme, dark) {
            Column(Modifier.width(320.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Panel(kind = PanelKind.Inset) {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(
                            "Continue", {}, Modifier.fillMaxWidth().testTag("disabled-primary"),
                            enabled = false, kind = ButtonKind.Primary,
                        )
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            IconButton(RoseIcon.Loop, "Loop", {}, Modifier.testTag("checked-icon"), checked = true)
                            IconButton(
                                RoseIcon.Play, "Play", {}, Modifier.testTag("disabled-prominent"),
                                enabled = false, prominent = true,
                            )
                        }
                        Panel(Modifier.fillMaxWidth().testTag("nested-inset"), kind = PanelKind.Inset) {
                            Text("Passage")
                        }
                    }
                }
                Panel(kind = PanelKind.Raised) {
                    Panel(Modifier.fillMaxWidth().testTag("nested-raised"), kind = PanelKind.Raised) {
                        Text("Chapter")
                    }
                }
            }
        }

        val outline = MinimalTheme.tokens(dark).colors.outline
        listOf("disabled-primary", "checked-icon", "disabled-prominent", "nested-inset", "nested-raised").forEach { tag ->
            assertLeftEdge(tag, outline)
        }
    }

    private fun assertLeftEdge(tag: String, color: Color) {
        val pixels = rule.onNodeWithTag(tag).captureToImage().toPixelMap()
        assertEquals("Left edge of $tag", color.toArgb(), pixels[1, pixels.height / 2].toArgb())
    }

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun modes() = ThemeCases.modes()
    }
}
