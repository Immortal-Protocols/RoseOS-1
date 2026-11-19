package app.rose.starters.matrix

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.isSystemInDarkTheme
import app.rose.design.RoseTheme

/**
 * Opens one combination: `adb shell am start -n app.rose.starters.matrix/.MatrixActivity --es template player
 * --es theme minimal [--ez dark true]`. Defaults to the first pilot of each. The hookup is the same line every
 * generated app uses: RoseTheme(theme) { template.App() }.
 */
class MatrixActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val template = Pilots.template(intent.getStringExtra("template")) ?: Pilots.templates.first()
        val theme = Pilots.theme(intent.getStringExtra("theme")) ?: Pilots.themes.first()
        val forceDark = if (intent.hasExtra("dark")) intent.getBooleanExtra("dark", false) else null
        setContent {
            RoseTheme(theme, dark = forceDark ?: isSystemInDarkTheme()) { template.App() }
        }
    }
}
