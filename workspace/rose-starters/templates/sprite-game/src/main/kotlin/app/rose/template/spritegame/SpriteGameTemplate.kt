package app.rose.template.spritegame

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.rose.design.Button
import app.rose.design.ButtonKind
import app.rose.design.Direction
import app.rose.design.DirectionPad
import app.rose.design.Panel
import app.rose.design.RoseIcon
import app.rose.design.RoseTheme
import app.rose.design.Screen
import app.rose.design.Stat
import app.rose.design.Template
import app.rose.design.Text
import kotlinx.coroutines.delay

/** A short maze game with a saveable round and stock controls that follow the active theme. */
object SpriteGameTemplate : Template {
    override val id = "sprite-game"
    override val displayName = "Sprite game"
    override val implemented = true

    @Composable
    override fun App(modifier: Modifier) {
        var round by rememberSaveable(stateSaver = RoundSaver) { mutableStateOf(GameRound()) }
        var heldDirections by remember { mutableStateOf(emptyList<Direction>()) }

        fun move(direction: Direction) {
            round = round.step(direction)
            if (!round.isPlaying) heldDirections = emptyList()
        }

        // Move once on press, including an accessibility click. Only physical holds repeat.
        val heldDirection = heldDirections.lastOrNull()
        LaunchedEffect(heldDirection) {
            if (heldDirection == null) return@LaunchedEffect
            delay(250)
            while (round.isPlaying && heldDirections.lastOrNull() == heldDirection) {
                move(heldDirection)
                delay(140)
            }
        }

        val spacing = RoseTheme.spacing
        Screen(title = "Beacon Run", modifier = modifier) { padding ->
            Box(
                Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState()),
                contentAlignment = Alignment.TopCenter,
            ) {
                Column(
                    Modifier.widthIn(max = 460.dp).fillMaxWidth().padding(spacing.l),
                    verticalArrangement = Arrangement.spacedBy(spacing.m),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    GameHud(round)
                    BeaconPlayfield(round, Modifier.fillMaxWidth())
                    DirectionPad(
                        onDirection = { direction, down ->
                            if (!down) {
                                heldDirections = heldDirections - direction
                            } else if (round.isPlaying && direction !in heldDirections) {
                                heldDirections = heldDirections + direction
                                move(direction)
                            }
                        },
                        enabled = round.isPlaying,
                    )
                    Button(
                        label = "Restart",
                        onClick = {
                            heldDirections = emptyList()
                            round = GameRound()
                        },
                        kind = if (round.isPlaying) ButtonKind.Secondary else ButtonKind.Primary,
                        icon = RoseIcon.Restart,
                    )
                }
            }
        }
    }
}

@Composable
private fun GameHud(round: GameRound) {
    val spacing = RoseTheme.spacing
    val beaconCount = BeaconScene.beaconCount
    val (mission, guidance) = when {
        round.status == RoundStatus.Lost -> "Signal lost" to "You hit spikes. Restart to try again."
        round.status == RoundStatus.Complete -> "Complete" to "All beacons delivered. Restart to play again."
        round.gateOpen -> "Gate open" to "All beacons found. Reach the gate in the upper right."
        else -> "Exploring" to "Collect every beacon, then reach the gate. Avoid the spikes."
    }
    Panel(Modifier.fillMaxWidth()) {
        Column(verticalArrangement = Arrangement.spacedBy(spacing.s)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(spacing.m),
            ) {
                Stat(
                    label = "Beacons",
                    value = "${round.collectedCount} / $beaconCount",
                    modifier = Modifier.weight(1f).semantics { liveRegion = LiveRegionMode.Polite },
                    progress = round.collectedCount.toFloat() / beaconCount,
                )
                Stat(
                    label = "Mission",
                    value = mission,
                    modifier = Modifier.weight(1f).semantics { liveRegion = LiveRegionMode.Polite },
                )
            }
            Text(guidance)
        }
    }
}

private val RoundSaver = listSaver<GameRound, Any>(
    save = { listOf(it.position.column, it.position.row, it.beaconBits, it.status.name) },
    restore = {
        GameRound(
            position = Cell(it[0] as Int, it[1] as Int),
            beaconBits = it[2] as Int,
            status = RoundStatus.valueOf(it[3] as String),
        )
    },
)
