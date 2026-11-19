package app.rose.design.testing

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.toPixelMap
import org.junit.Assert.assertTrue

/**
 * Pixel checks on a rendered control, for what semantics cannot see: is a track or an edge visible against the
 * surface behind it, and does the drawing fill the height it was given. Thresholds are lenient on purpose: they catch
 * "drew nothing you can see" and "drew a fixed-height strip", not taste.
 */
object Pixels {
    /** A part of the image, as fractions of its width and height. */
    class Region(val x0: Float = 0f, val x1: Float = 1f, val y0: Float = 0f, val y1: Float = 1f)

    /** Visibly different from [host]: at least 1.2:1 contrast (anti-aliased edges and 45% outlines pass). */
    private fun visible(pixel: Color, host: Color) = Contrast.ratio(pixel.copy(alpha = 1f), host) >= 1.2

    /** At least a few pixels in [region] stand out from [host]. */
    fun requireVisible(what: String, image: ImageBitmap, host: Color, region: Region = Region()) {
        val map = image.toPixelMap()
        val xs = (region.x0 * map.width).toInt() until (region.x1 * map.width).toInt().coerceAtMost(map.width)
        val ys = (region.y0 * map.height).toInt() until (region.y1 * map.height).toInt().coerceAtMost(map.height)
        var seen = 0
        for (y in ys) for (x in xs) if (visible(map[x, y], host)) seen++
        val needed = maxOf(4, xs.count() * ys.count() / 200)
        assertTrue("$what: $seen pixels stand out from the surface behind it, needs $needed (it is invisible there)", seen >= needed)
    }

    /** The rows that hold anything visible span at least [fraction] of the image height. */
    fun requireSpan(what: String, image: ImageBitmap, host: Color, fraction: Float) {
        val map = image.toPixelMap()
        val rows = (0 until map.height).filter { y -> (0 until map.width).any { x -> visible(map[x, y], host) } }
        val span = if (rows.isEmpty()) 0 else rows.last() - rows.first() + 1
        assertTrue(
            "$what: draws across $span of ${map.height} px rows, needs ${(fraction * 100).toInt()}% (it ignores the height it was given)",
            span >= fraction * map.height,
        )
    }
}
