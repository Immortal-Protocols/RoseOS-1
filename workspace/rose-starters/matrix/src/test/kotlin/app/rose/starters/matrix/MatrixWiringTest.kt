package app.rose.starters.matrix

import androidx.compose.ui.test.isHeading
import androidx.compose.ui.test.junit4.createComposeRule
import app.rose.design.BaselineTheme
import app.rose.design.Template
import app.rose.design.Theme
import app.rose.design.testing.setThemedContent
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * All nine pilot combinations (and each template under Baseline) compose with the same one-line hookup and show a
 * titled screen, placeholders included. This is wiring only; each template's Scenario checks behaviour.
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37], qualifiers = "w411dp-h891dp")
class MatrixWiringTest(@Suppress("unused") private val name: String, private val template: Template, private val theme: Theme) {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun composesUnderTheTheme() {
        rule.setThemedContent(theme, dark = false) { template.App() }
        assertEquals("one titled screen", 1, rule.onAllNodes(isHeading()).fetchSemanticsNodes().size)
    }

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "{0}")
        fun cases(): List<Array<Any>> = Pilots.templates.flatMap { t ->
            (Pilots.themes + BaselineTheme).map { th -> arrayOf("${t.id} x ${th.id}", t, th) }
        }
    }
}
