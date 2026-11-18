package app.rose.design

import android.graphics.Bitmap
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.unit.dp
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File

/** Renders every baseline icon with its name to build/screenshots/icons.png, for review. */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [37], qualifiers = "w411dp-h891dp-xxhdpi")
class IconSheet {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun render() {
        rule.setContent {
            RoseTheme(BaselineTheme, dark = false) {
                Screen("Icons") { padding ->
                    FlowRow(Modifier.padding(padding).padding(12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        RoseIcon.entries.forEach { icon ->
                            Column(Modifier.width(88.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                                Icon(icon, icon.name, size = 36.dp)
                                Text(icon.name, role = TextRole.Caption)
                            }
                        }
                    }
                }
            }
        }
        val out = File("build/screenshots").apply { mkdirs() }
        File(out, "icons.png").outputStream().use { rule.onRoot().captureToImage().asAndroidBitmap().compress(Bitmap.CompressFormat.PNG, 100, it) }
    }
}
