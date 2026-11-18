package app.rose.theme.skeuomorphic

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.rose.design.Controls
import app.rose.design.RoseColors
import app.rose.design.RoseShapes
import app.rose.design.RoseTokens
import app.rose.design.RoseType
import app.rose.design.Theme

/** Enamel housings, beveled keys and recessed tracks for Rose's stock controls. */
object SkeuomorphicTheme : Theme {
    override val id = "skeuomorphic"
    override val displayName = "Skeuomorphic"
    override val implemented = true
    override val controls: Controls = SkeuomorphicControls

    private val type = RoseType(
        display = TextStyle(fontFamily = FontFamily.Serif, fontWeight = FontWeight.Bold, fontSize = 34.sp, lineHeight = 40.sp),
        title = TextStyle(fontFamily = FontFamily.Serif, fontWeight = FontWeight.Bold, fontSize = 24.sp, lineHeight = 30.sp),
        body = TextStyle(fontSize = 16.sp, lineHeight = 23.sp),
        label = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 14.sp, lineHeight = 19.sp),
        caption = TextStyle(fontWeight = FontWeight.Medium, fontSize = 12.sp, lineHeight = 16.sp),
    )
    private val shapes = RoseShapes(RoundedCornerShape(8.dp), RoundedCornerShape(12.dp), RoundedCornerShape(18.dp))
    private val light = RoseTokens(
        colors = RoseColors(
            background = Color(0xFFE6DFD2), onBackground = Color(0xFF251E19),
            surface = Color(0xFFF5EEE1), onSurface = Color(0xFF251E19), muted = Color(0xFF615145),
            surfaceVariant = Color(0xFFD9CFC0), onSurfaceVariant = Color(0xFF31261E),
            accent = Color(0xFF854A29), onAccent = Color(0xFFFFF8EA), outline = Color(0xFF806D5B),
            danger = Color(0xFF972E2C), onDanger = Color(0xFFFFF3EE), isDark = false,
        ),
        type = type,
        shapes = shapes,
    )
    private val dark = RoseTokens(
        colors = RoseColors(
            background = Color(0xFF1B2025), onBackground = Color(0xFFF4EBDD),
            surface = Color(0xFF2B3238), onSurface = Color(0xFFF4EBDD), muted = Color(0xFFBDB3A6),
            surfaceVariant = Color(0xFF20262B), onSurfaceVariant = Color(0xFFEFE3D3),
            accent = Color(0xFFE7AE76), onAccent = Color(0xFF2C190D), outline = Color(0xFF8F8B83),
            danger = Color(0xFFF3A39B), onDanger = Color(0xFF371714), isDark = true,
        ),
        type = type,
        shapes = shapes,
    )

    override fun tokens(dark: Boolean): RoseTokens = if (dark) this.dark else light
}
