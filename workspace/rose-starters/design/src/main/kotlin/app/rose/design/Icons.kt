package app.rose.design

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathBuilder
import androidx.compose.ui.graphics.vector.path
import androidx.compose.ui.unit.dp

/**
 * The icons a template may ask for, by meaning. A theme draws them through [Controls.Icon]: the baseline vectors
 * below ([RoseIcons.vector]), or its own artwork for the same meaning. Adding a meaning is a framework change.
 */
enum class RoseIcon {
    Back, Close, Add, Remove, Check, More, Search, Tune,
    Play, Pause, Restart, Loop, SkipBack, SkipForward,
    Like, Comment, Share, Person, Image,
    Up, Down, Left, Right, Jump,
}

/** Rose's baseline line icons on a 24dp grid. Black fill/stroke: tint them with ColorFilter or Icon's tint. */
object RoseIcons {
    private val cache = HashMap<RoseIcon, ImageVector>()

    fun vector(icon: RoseIcon): ImageVector = synchronized(cache) { cache.getOrPut(icon) { build(icon) } }

    private fun build(icon: RoseIcon): ImageVector = when (icon) {
        RoseIcon.Back -> line(icon) { moveTo(20f, 12f); horizontalLineTo(4f); moveTo(10f, 6f); lineTo(4f, 12f); lineTo(10f, 18f) }
        RoseIcon.Close -> line(icon) { moveTo(6f, 6f); lineTo(18f, 18f); moveTo(18f, 6f); lineTo(6f, 18f) }
        RoseIcon.Add -> line(icon) { moveTo(12f, 5f); verticalLineTo(19f); moveTo(5f, 12f); horizontalLineTo(19f) }
        RoseIcon.Remove -> line(icon) { moveTo(5f, 12f); horizontalLineTo(19f) }
        RoseIcon.Check -> line(icon) { moveTo(5f, 12.5f); lineTo(10f, 17.5f); lineTo(19.5f, 7f) }
        RoseIcon.More -> solid(icon) { dot(5.5f, 12f); dot(12f, 12f); dot(18.5f, 12f) }
        RoseIcon.Search -> line(icon) { circle(10.5f, 10.5f, 6.5f); moveTo(15.5f, 15.5f); lineTo(20.5f, 20.5f) }
        RoseIcon.Tune -> line(icon) {
            moveTo(4f, 7f); horizontalLineTo(13f); moveTo(17f, 7f); horizontalLineTo(20f); moveTo(15f, 4.5f); verticalLineTo(9.5f)
            moveTo(4f, 17f); horizontalLineTo(7f); moveTo(11f, 17f); horizontalLineTo(20f); moveTo(9f, 14.5f); verticalLineTo(19.5f)
        }
        RoseIcon.Play -> solid(icon) { moveTo(8f, 5f); lineTo(19f, 12f); lineTo(8f, 19f); close() }
        RoseIcon.Pause -> solid(icon) { rect(6f, 5f, 4f, 14f); rect(14f, 5f, 4f, 14f) }
        RoseIcon.Restart -> line(icon) {
            moveTo(4.95f, 9.43f); arcTo(7.5f, 7.5f, 0f, isMoreThanHalf = true, isPositiveArc = true, x1 = 4.95f, y1 = 14.57f)
            moveTo(4.4f, 4.6f); lineTo(4.95f, 9.43f); lineTo(9.8f, 8.7f)
        }
        RoseIcon.Loop -> line(icon) {
            moveTo(17f, 3f); lineTo(20f, 6f); lineTo(17f, 9f); moveTo(4f, 11f); verticalLineTo(10f)
            arcTo(4f, 4f, 0f, isMoreThanHalf = false, isPositiveArc = true, x1 = 8f, y1 = 6f); horizontalLineTo(20f)
            moveTo(7f, 21f); lineTo(4f, 18f); lineTo(7f, 15f); moveTo(20f, 13f); verticalLineTo(14f)
            arcTo(4f, 4f, 0f, isMoreThanHalf = false, isPositiveArc = true, x1 = 16f, y1 = 18f); horizontalLineTo(4f)
        }
        RoseIcon.SkipBack -> solid(icon) { moveTo(11f, 6f); lineTo(4f, 12f); lineTo(11f, 18f); close(); moveTo(20f, 6f); lineTo(13f, 12f); lineTo(20f, 18f); close() }
        RoseIcon.SkipForward -> solid(icon) { moveTo(13f, 6f); lineTo(20f, 12f); lineTo(13f, 18f); close(); moveTo(4f, 6f); lineTo(11f, 12f); lineTo(4f, 18f); close() }
        RoseIcon.Like -> line(icon) {
            moveTo(12f, 20f); lineTo(4.6f, 12.6f)
            arcTo(4.4f, 4.4f, 0f, isMoreThanHalf = false, isPositiveArc = true, x1 = 12f, y1 = 6.8f)
            arcTo(4.4f, 4.4f, 0f, isMoreThanHalf = false, isPositiveArc = true, x1 = 19.4f, y1 = 12.6f)
            close()
        }
        RoseIcon.Comment -> line(icon) { moveTo(4f, 5f); horizontalLineTo(20f); verticalLineTo(16f); horizontalLineTo(11f); lineTo(7f, 19.5f); verticalLineTo(16f); horizontalLineTo(4f); close() }
        RoseIcon.Share -> line(icon) { moveTo(12f, 3.5f); verticalLineTo(15f); moveTo(7.5f, 8f); lineTo(12f, 3.5f); lineTo(16.5f, 8f); moveTo(5f, 13f); verticalLineTo(20f); horizontalLineTo(19f); verticalLineTo(13f) }
        RoseIcon.Person -> line(icon) { circle(12f, 8f, 4f); moveTo(4.5f, 20.5f); arcTo(7.5f, 7f, 0f, isMoreThanHalf = false, isPositiveArc = true, x1 = 19.5f, y1 = 20.5f) }
        RoseIcon.Image -> line(icon) { moveTo(4f, 5f); horizontalLineTo(20f); verticalLineTo(19f); horizontalLineTo(4f); close(); moveTo(4f, 16.5f); lineTo(9f, 11.5f); lineTo(13f, 15.5f); lineTo(16f, 12.5f); lineTo(20f, 16.5f) }
        RoseIcon.Up -> line(icon) { moveTo(6f, 15f); lineTo(12f, 9f); lineTo(18f, 15f) }
        RoseIcon.Down -> line(icon) { moveTo(6f, 9f); lineTo(12f, 15f); lineTo(18f, 9f) }
        RoseIcon.Left -> line(icon) { moveTo(15f, 6f); lineTo(9f, 12f); lineTo(15f, 18f) }
        RoseIcon.Right -> line(icon) { moveTo(9f, 6f); lineTo(15f, 12f); lineTo(9f, 18f) }
        RoseIcon.Jump -> line(icon) { moveTo(12f, 20f); verticalLineTo(7f); moveTo(6.5f, 12.5f); lineTo(12f, 7f); lineTo(17.5f, 12.5f); moveTo(5f, 3.5f); horizontalLineTo(19f) }
    }

    private fun builder(icon: RoseIcon) =
        ImageVector.Builder(name = "Rose.${icon.name}", defaultWidth = 24.dp, defaultHeight = 24.dp, viewportWidth = 24f, viewportHeight = 24f)

    private fun line(icon: RoseIcon, body: PathBuilder.() -> Unit): ImageVector = builder(icon).path(
        stroke = SolidColor(Color.Black), strokeLineWidth = 2f, strokeLineCap = StrokeCap.Round, strokeLineJoin = StrokeJoin.Round, pathBuilder = body,
    ).build()

    private fun solid(icon: RoseIcon, body: PathBuilder.() -> Unit): ImageVector =
        builder(icon).path(fill = SolidColor(Color.Black), pathBuilder = body).build()

    private fun PathBuilder.circle(cx: Float, cy: Float, r: Float) {
        moveTo(cx - r, cy)
        arcTo(r, r, 0f, isMoreThanHalf = true, isPositiveArc = true, x1 = cx + r, y1 = cy)
        arcTo(r, r, 0f, isMoreThanHalf = true, isPositiveArc = true, x1 = cx - r, y1 = cy)
        close()
    }

    private fun PathBuilder.dot(cx: Float, cy: Float) = circle(cx, cy, 1.8f)

    private fun PathBuilder.rect(x: Float, y: Float, w: Float, h: Float) {
        moveTo(x, y); horizontalLineTo(x + w); verticalLineTo(y + h); horizontalLineTo(x); close()
    }
}
