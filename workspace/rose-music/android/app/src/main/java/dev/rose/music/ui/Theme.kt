package dev.rose.music.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import dev.rose.music.CoverColors
import dev.rose.music.R

object Ink {
    val Night = Color(0xFF07070B)
    val Surface = Color(0xFF111118)
    val Raised = Color(0xFF1A1A24)
    val Hairline = Color(0x1AFFFFFF)
    val Text = Color(0xFFF5F2FB)
    val Muted = Color(0xFF9C99AC)
    val Faint = Color(0xFF5E5B6C)
    val Live = Color(0xFF6CF0B4)
    val Warn = Color(0xFFFFC56E)
    val Error = Color(0xFFFF7A90)
}

@OptIn(androidx.compose.ui.text.ExperimentalTextApi::class)
private fun grotesk(w: Int) = Font(R.font.space_grotesk, FontWeight(w), variationSettings = FontVariation.Settings(FontVariation.weight(w)))

@OptIn(androidx.compose.ui.text.ExperimentalTextApi::class)
private fun inter(w: Int) = Font(R.font.inter, FontWeight(w), variationSettings = FontVariation.Settings(FontVariation.weight(w)))

val Display = FontFamily(grotesk(400), grotesk(500), grotesk(600), grotesk(700))
val Body = FontFamily(inter(400), inter(500), inter(600), inter(700))

object Type {
    val Hero = TextStyle(fontFamily = Display, fontWeight = FontWeight.Bold, fontSize = 44.sp, lineHeight = 44.sp, letterSpacing = (-0.04).em)
    val Title = TextStyle(fontFamily = Display, fontWeight = FontWeight.Bold, fontSize = 28.sp, lineHeight = 32.sp, letterSpacing = (-0.03).em)
    val Row = TextStyle(fontFamily = Body, fontWeight = FontWeight.SemiBold, fontSize = 16.sp, lineHeight = 20.sp, letterSpacing = (-0.01).em)
    val Meta = TextStyle(fontFamily = Body, fontWeight = FontWeight.Medium, fontSize = 13.sp, lineHeight = 16.sp)
    val Label = TextStyle(fontFamily = Body, fontWeight = FontWeight.SemiBold, fontSize = 14.sp, lineHeight = 18.sp)
    val Eyebrow = TextStyle(fontFamily = Body, fontWeight = FontWeight.SemiBold, fontSize = 11.sp, letterSpacing = 0.14.em)
    val Mono = TextStyle(fontFamily = Display, fontWeight = FontWeight.Medium, fontSize = 12.sp, letterSpacing = 0.02.em)
}

/** The colours of what's playing (or the library's lead cover), animated by the screens that read them. */
val LocalCoverColors = staticCompositionLocalOf { CoverColors.Default }

@Composable
fun RoseMusicTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            background = Ink.Night, surface = Ink.Surface, onBackground = Ink.Text, onSurface = Ink.Text,
            primary = CoverColors.Default.accent, onPrimary = Ink.Night,
        ),
        content = content,
    )
}
