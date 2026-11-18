package app.rose.template.player

import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import app.rose.design.Screen
import app.rose.design.Template
import app.rose.design.Text

/**
 * OWNER: Player template engineer. Play, scrub and loop a passage; the timeline is the main interaction.
 *
 * The Player starter. Keep the object, its [id] and its place in the matrix; everything behind [App] is yours
 * (screens, state, sample content). Draw only with app.rose.design. Set [implemented] to true when it works.
 */
object PlayerTemplate : Template {
    override val id = "player"
    override val displayName = "Player"
    override val implemented = false // placeholder: the matrix skips it until this is true

    @Composable
    override fun App(modifier: Modifier) {
        Screen("Player", modifier) { padding -> Text("Player template: not implemented yet", Modifier.padding(padding)) }
    }
}
