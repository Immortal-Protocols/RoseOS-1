package app.rose.template.spritegame

import app.rose.design.Direction

internal data class Cell(val column: Int, val row: Int) {
    fun next(direction: Direction): Cell = when (direction) {
        Direction.Up -> copy(row = row - 1)
        Direction.Down -> copy(row = row + 1)
        Direction.Left -> copy(column = column - 1)
        Direction.Right -> copy(column = column + 1)
    }
}

internal enum class Tile { Floor, Wall, Beacon, Spikes, Gate }

internal object BeaconScene {
    private val map = listOf(
        "#########",
        "#S.B#..G#",
        "#...#.!.#",
        "#...B...#",
        "#..##...#",
        "#B..!...#",
        "#########",
    )
    val columns = map.first().length
    val rows = map.size
    val start = cellsMarked('S').single()
    private val beacons = cellsMarked('B')
    val beaconCount = beacons.size

    private fun cellsMarked(marker: Char): List<Cell> = map.flatMapIndexed { row, tiles ->
        tiles.mapIndexedNotNull { column, tile -> Cell(column, row).takeIf { tile == marker } }
    }

    fun tileAt(cell: Cell): Tile = when (map.getOrNull(cell.row)?.getOrNull(cell.column)) {
        null, '#' -> Tile.Wall
        'B' -> Tile.Beacon
        '!' -> Tile.Spikes
        'G' -> Tile.Gate
        else -> Tile.Floor
    }

    fun beaconBit(cell: Cell): Int = 1 shl beacons.indexOf(cell)
}

internal enum class RoundStatus { Exploring, Lost, Complete }

internal data class GameRound(
    val position: Cell = BeaconScene.start,
    val beaconBits: Int = 0,
    val status: RoundStatus = RoundStatus.Exploring,
) {
    val collectedCount get() = beaconBits.countOneBits()
    val gateOpen get() = collectedCount == BeaconScene.beaconCount
    val isPlaying get() = status == RoundStatus.Exploring

    fun hasCollected(beacon: Cell): Boolean = (beaconBits and BeaconScene.beaconBit(beacon)) != 0

    fun step(direction: Direction): GameRound {
        if (!isPlaying) return this
        val target = position.next(direction)
        return when (BeaconScene.tileAt(target)) {
            Tile.Wall -> this
            Tile.Gate -> if (gateOpen) copy(position = target, status = RoundStatus.Complete) else this
            Tile.Spikes -> copy(position = target, status = RoundStatus.Lost)
            Tile.Beacon -> copy(position = target, beaconBits = beaconBits or BeaconScene.beaconBit(target))
            Tile.Floor -> copy(position = target)
        }
    }

    fun describePosition(): String {
        val location = "Column ${position.column + 1}, row ${position.row + 1}."
        if (!isPlaying) return location
        val neighbors = Direction.entries.joinToString(". ", postfix = ".") { direction ->
            val neighbor = position.next(direction)
            val terrain = when (BeaconScene.tileAt(neighbor)) {
                Tile.Wall -> "a wall"
                Tile.Gate -> if (gateOpen) "the open gate" else "the locked gate"
                Tile.Spikes -> "spikes"
                Tile.Beacon -> if (hasCollected(neighbor)) "a clear tile" else "a beacon"
                Tile.Floor -> "a clear tile"
            }
            "${direction.label} leads to $terrain"
        }
        return "$location $neighbors"
    }
}
