package app.rose.template.spritegame

import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import app.rose.design.Screen
import app.rose.design.Template
import app.rose.design.Text

/**
 * OWNER: Sprite game template engineer. Play a short scene with movement, collision and restart; themeable controls and HUD, gameplay preserved.
 *
 * The Sprite game starter. Keep the object, its [id] and its place in the matrix; everything behind [App] is yours
 * (screens, state, sample content). Draw only with app.rose.design. Set [implemented] to true when it works.
 */
object SpriteGameTemplate : Template {
    override val id = "sprite-game"
    override val displayName = "Sprite game"
    override val implemented = false // placeholder: the matrix skips it until this is true

    @Composable
    override fun App(modifier: Modifier) {
        Screen("Sprite game", modifier) { padding -> Text("Sprite game template: not implemented yet", Modifier.padding(padding)) }
    }
}
