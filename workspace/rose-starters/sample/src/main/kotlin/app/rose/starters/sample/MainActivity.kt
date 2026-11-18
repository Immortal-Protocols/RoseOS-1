package app.rose.starters.sample

import app.rose.starters.examples.PaperTheme
import app.rose.starters.examples.TallyTemplate
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import app.rose.design.BaselineTheme
import app.rose.design.RoseTheme

/**
 * The complete theme hookup. To swap the theme, change the one marked line, e.g. `BaselineTheme` → `PaperTheme`:
 * the template, its state and its behaviour stay exactly the same (TallyBehaviorTest runs the same script under both).
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            RoseTheme(BaselineTheme) { // ← the one line: BaselineTheme or PaperTheme (or any Theme)
                TallyTemplate.App()
            }
        }
    }
}
