package app.rose.template.spritegame

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.translate
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.unit.dp
import app.rose.design.RoseTheme

@Composable
internal fun BeaconPlayfield(round: GameRound, modifier: Modifier = Modifier) {
    val colors = RoseTheme.colors
    val shape = RoseTheme.shapes.medium
    Canvas(
        modifier
            .aspectRatio(BeaconScene.columns.toFloat() / BeaconScene.rows)
            .clip(shape)
            .border(1.dp, colors.outline, shape)
            .semantics {
                contentDescription = "Playfield"
                stateDescription = round.describePosition()
            },
    ) {
        drawRect(colors.surfaceVariant)
        val cellWidth = size.width / BeaconScene.columns
        val cellHeight = size.height / BeaconScene.rows
        val cellSize = Size(cellWidth, cellHeight)

        repeat(BeaconScene.rows) { row ->
            repeat(BeaconScene.columns) { column ->
                val cell = Cell(column, row)
                translate(left = column * cellWidth, top = row * cellHeight) {
                    val tile = BeaconScene.tileAt(cell)
                    if (tile == Tile.Wall) {
                        drawRect(colors.surface, size = cellSize)
                    } else if ((row + column) % 2 == 0) {
                        drawRect(colors.onSurfaceVariant.copy(alpha = 0.06f), size = cellSize)
                    }
                    when (tile) {
                        Tile.Wall -> drawSprite(WallSprite, cellSize, colors.onSurface.copy(alpha = 0.22f), colors.outline)
                        Tile.Spikes -> drawSprite(SpikeSprite, cellSize, colors.danger, colors.onDanger)
                        Tile.Beacon -> if (!round.hasCollected(cell)) {
                            drawSprite(BeaconSprite, cellSize, colors.accent, colors.onAccent)
                        }
                        Tile.Gate -> if (round.gateOpen) {
                            drawSprite(OpenGateSprite, cellSize, colors.accent, colors.onAccent)
                        } else {
                            drawSprite(ClosedGateSprite, cellSize, colors.onSurfaceVariant, colors.surfaceVariant)
                        }
                        Tile.Floor -> Unit
                    }
                    if (cell == round.position) {
                        val lost = round.status == RoundStatus.Lost
                        drawSprite(
                            RoverSprite, cellSize,
                            body = if (lost) colors.danger else colors.accent,
                            detail = if (lost) colors.onDanger else colors.onAccent,
                        )
                    }
                }
            }
        }
    }
}

private fun DrawScope.drawSprite(rows: List<String>, cell: Size, body: Color, detail: Color) {
    val pixel = Size(cell.width / 8, cell.height / 8)
    rows.forEachIndexed { y, row ->
        row.forEachIndexed spritePixel@{ x, mark ->
            val color = when (mark) {
                'x' -> body
                'o' -> detail
                else -> return@spritePixel
            }
            drawRect(color, topLeft = Offset(x * pixel.width, y * pixel.height), size = pixel)
        }
    }
}

private val WallSprite = listOf(
    "xxxxxxxx",
    "x......x",
    "x......x",
    "x......x",
    "oo...ooo",
    "x......x",
    "x......x",
    "xxxxxxxx",
)

private val RoverSprite = listOf(
    "...xx...",
    "....x...",
    ".xxxxxx.",
    ".xoxxox.",
    ".xoxxox.",
    ".xxxxxx.",
    "..x..x..",
    "........",
)

private val BeaconSprite = listOf(
    "........",
    "...xx...",
    "..xxxx..",
    ".xxoxxx.",
    ".xxxoxx.",
    "..xxxx..",
    "...xx...",
    "........",
)

private val SpikeSprite = listOf(
    "........",
    "........",
    ".x.x.x..",
    ".x.x.x..",
    ".xxxxxx.",
    ".xoxoxo.",
    ".xxxxxx.",
    "........",
)

private val ClosedGateSprite = listOf(
    "........",
    "..xxxx..",
    ".xxxxxx.",
    ".xoooox.",
    ".xxxxxx.",
    ".xoooox.",
    ".xxxxxx.",
    "........",
)

private val OpenGateSprite = listOf(
    "........",
    "..xxxx..",
    ".xxooxx.",
    ".xo..ox.",
    ".x....x.",
    ".x....x.",
    ".x....x.",
    "........",
)
