package app.rose.design.testing

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.graphics.luminance
import org.junit.Assert.assertTrue

/** WCAG 2 contrast ratios, so "readable" and "clear selection" are checked, not eyeballed. */
object Contrast {
    fun ratio(a: Color, b: Color): Double {
        val bg = b.compositeOver(Color.White)
        val fg = a.compositeOver(bg)
        val l1 = fg.luminance().toDouble()
        val l2 = bg.luminance().toDouble()
        return (maxOf(l1, l2) + 0.05) / (minOf(l1, l2) + 0.05)
    }

    fun require(what: String, foreground: Color, background: Color, minimum: Double) {
        val r = ratio(foreground, background)
        assertTrue("$what: contrast %.2f:1, needs at least %.1f:1".format(r, minimum), r + 1e-6 >= minimum)
    }
}
