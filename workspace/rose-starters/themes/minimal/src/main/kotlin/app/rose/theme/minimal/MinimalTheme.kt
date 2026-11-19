package app.rose.theme.minimal

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.rose.design.Controls
import app.rose.design.RoseColors
import app.rose.design.RoseShapes
import app.rose.design.RoseSpacing
import app.rose.design.RoseTokens
import app.rose.design.RoseType
import app.rose.design.Theme

/** A typography-led theme with flat surfaces, fine rules and explicit selection marks. */
object MinimalTheme : Theme {
    override val id = "minimal"
    override val displayName = "Minimal"
    override val implemented = true
    override val controls: Controls = MinimalControls

    private val lightColors = RoseColors(
        background = Color(0xFFFCFBF8),
        onBackground = Color(0xFF182320),
        surface = Color(0xFFF7F6F2),
        onSurface = Color(0xFF182320),
        muted = Color(0xFF535D59),
        surfaceVariant = Color(0xFFEBEDE9),
        onSurfaceVariant = Color(0xFF25342F),
        accent = Color(0xFF155D55),
        onAccent = Color(0xFFFFFFFF),
        outline = Color(0xFF738078),
        danger = Color(0xFFA12C37),
        onDanger = Color(0xFFFFFFFF),
        isDark = false,
    )

    private val darkColors = RoseColors(
        background = Color(0xFF151918),
        onBackground = Color(0xFFF0F2EA),
        surface = Color(0xFF1B211F),
        onSurface = Color(0xFFF0F2EA),
        muted = Color(0xFFAFB9B0),
        surfaceVariant = Color(0xFF272F2C),
        onSurfaceVariant = Color(0xFFE3EBE3),
        accent = Color(0xFF8ED8C6),
        onAccent = Color(0xFF11322B),
        outline = Color(0xFF81958B),
        danger = Color(0xFFFFA6AA),
        onDanger = Color(0xFF4D101B),
        isDark = true,
    )

    private val type = RoseType(
        display = TextStyle(
            fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.Light,
            fontSize = 38.sp, lineHeight = 43.sp, letterSpacing = (-0.9).sp,
        ),
        title = TextStyle(
            fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.Medium,
            fontSize = 23.sp, lineHeight = 29.sp, letterSpacing = (-0.3).sp,
        ),
        body = TextStyle(fontFamily = FontFamily.SansSerif, fontSize = 16.sp, lineHeight = 24.sp),
        label = TextStyle(
            fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.Medium,
            fontSize = 15.sp, lineHeight = 21.sp,
        ),
        caption = TextStyle(
            fontFamily = FontFamily.Monospace, fontSize = 12.sp, lineHeight = 18.sp,
        ),
    )
    private val shapes = RoseShapes(
        small = RoundedCornerShape(3.dp),
        medium = RectangleShape,
        large = RoundedCornerShape(6.dp),
    )
    private val spacing = RoseSpacing(xs = 4.dp, s = 8.dp, m = 12.dp, l = 20.dp, xl = 28.dp, xxl = 36.dp)
    private val light = RoseTokens(lightColors, type, shapes, spacing)
    private val dark = RoseTokens(darkColors, type, shapes, spacing)

    override fun tokens(dark: Boolean) = if (dark) this.dark else light
}
