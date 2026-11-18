package app.rose.template.feed

import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import app.rose.design.Screen
import app.rose.design.Template
import app.rose.design.Text

/**
 * OWNER: Feed template engineer. Scroll posts, open a detail, return to the same position.
 *
 * The Feed starter. Keep the object, its [id] and its place in the matrix; everything behind [App] is yours
 * (screens, state, sample content). Draw only with app.rose.design. Set [implemented] to true when it works.
 */
object FeedTemplate : Template {
    override val id = "feed"
    override val displayName = "Feed"
    override val implemented = false // placeholder: the matrix skips it until this is true

    @Composable
    override fun App(modifier: Modifier) {
        Screen("Feed", modifier) { padding -> Text("Feed template: not implemented yet", Modifier.padding(padding)) }
    }
}
