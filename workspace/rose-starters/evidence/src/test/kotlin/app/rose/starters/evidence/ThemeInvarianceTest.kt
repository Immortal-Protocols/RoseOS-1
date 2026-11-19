package app.rose.starters.evidence

import android.os.Build
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsNode
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.dp
import app.rose.design.Template
import app.rose.design.Theme
import app.rose.design.testing.setThemedContent
import app.rose.theme.material3.Material3Theme
import app.rose.theme.minimal.MinimalTheme
import app.rose.theme.skeuomorphic.SkeuomorphicTheme
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowMediaPlayer
import java.io.File

/**
 * Wave 3 behaviour evidence. One template's code under each pilot theme: the theme may change every pixel, but not
 * what a person can read and do. For each (template, theme, API) this checks the invariants every Rose app keeps and
 * writes a normalized semantics snapshot (texts, roles, actions and states; never sizes, positions, colours or
 * the order controls are laid out in) of the opening screen and after clicking each control, in alphabetical order. compareThemeSnapshots then requires the
 * three themes' snapshots to be identical. The screen is very tall so lazy lists compose every item: how many fit
 * must not depend on the theme's spacing.
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37], qualifiers = "w411dp-h6000dp")
class ThemeInvarianceTest(private val templateName: String, private val themeId: String) {
    @get:Rule
    val rule = createComposeRule()

    private val template: Template = Class.forName(templateName).getField("INSTANCE").get(null) as Template
    private val theme: Theme = THEMES.getValue(themeId)

    @Test
    fun sameBehaviourUnderEveryTheme() {
        // Any app that plays media through MediaPlayer gets a 30-second clip on the JVM, so its controls are live.
        ShadowMediaPlayer.setMediaInfoProvider { ShadowMediaPlayer.MediaInfo(30_000, 0) }
        rule.setThemedContent(theme, dark = false) { template.App() }
        val out = StringBuilder()
        out.append("## opening screen\n").append(snapshot())
        checkInvariants()
        // Alphabetical, not tree order: where a theme places a control is its layout, not the app's behaviour.
        val controls = clickables().map(::key).filter { it.isNotEmpty() }.distinct().sorted().take(MAX_CLICKS)
        for (control in controls) {
            val node = rule.onAllNodes(hasClickAction() and (hasText(control) or hasContentDescription(control)))
                .fetchSemanticsNodes().firstOrNull() ?: run { out.append("## $control: gone\n"); continue }
            rule.onAllNodes(SemanticsMatcher("id ${node.id}") { it.id == node.id })[0].performClick()
            rule.waitForIdle()
            out.append("## after $control\n").append(snapshot())
        }
        // The template's id is the writer's: it names a folder and goes into report.json, so it must be a plain
        // lowercase id (the same rule as starters.json), never a path or anything JSON would need to escape.
        assertTrue("$templateName: its id '${template.id}' is not a lowercase id", ID.matches(template.id))
        val dir = File(System.getProperty("rose.evidence.out") ?: "build/evidence", template.id).apply { mkdirs() }
        File(dir, "$themeId-api${Build.VERSION.SDK_INT}.txt").writeText(out.toString())
    }

    private fun nodes(): List<SemanticsNode> {
        val all = mutableListOf<SemanticsNode>()
        fun walk(n: SemanticsNode) { all += n; n.children.forEach(::walk) }
        walk(rule.onRoot().fetchSemanticsNode())
        return all
    }

    private fun clickables() = nodes().filter { SemanticsActions.OnClick in it.config && !it.config.contains(SemanticsProperties.Disabled) }

    private fun key(n: SemanticsNode): String {
        val c = n.config
        return c.getOrNull(SemanticsProperties.ContentDescription)?.joinToString(" ")
            ?: c.getOrNull(SemanticsProperties.Text)?.joinToString(" ") { it.text } ?: ""
    }

    /** Every clickable is labelled and big enough to touch, and the screen has a title. */
    private fun checkInvariants() {
        val all = nodes()
        assertTrue("$templateName under $themeId has a titled screen", all.any { SemanticsProperties.Heading in it.config })
        for (n in clickables()) {
            assertTrue("$templateName under $themeId: a clickable without a label (${n.config})", key(n).isNotBlank())
            val b = n.touchBoundsInRoot
            with(rule.density) {
                assertTrue("$templateName under $themeId: '${key(n)}' touch target ${b.width.toDp()}×${b.height.toDp()}",
                    b.width.toDp() >= 47.5.dp && b.height.toDp() >= 47.5.dp)
            }
        }
    }

    /** What a person can read and do, in tree order, one line per meaningful node; nothing about the look. */
    private fun snapshot(): String = nodes().mapNotNull { n ->
        val c = n.config
        val parts = mutableListOf<String>()
        c.getOrNull(SemanticsProperties.Role)?.let { parts += "role=$it" }
        c.getOrNull(SemanticsProperties.Text)?.let { t -> parts += "text=" + t.joinToString("|") { it.text } }
        c.getOrNull(SemanticsProperties.EditableText)?.let { parts += "edit=${it.text}" }
        c.getOrNull(SemanticsProperties.ContentDescription)?.let { parts += "desc=" + it.joinToString("|") }
        c.getOrNull(SemanticsProperties.StateDescription)?.let { parts += "state=$it" }
        c.getOrNull(SemanticsProperties.ToggleableState)?.let { parts += "toggle=$it" }
        c.getOrNull(SemanticsProperties.Selected)?.let { parts += "selected=$it" }
        c.getOrNull(SemanticsProperties.ProgressBarRangeInfo)?.let { parts += "progress=${it.current}/${it.range}/${it.steps}" }
        if (SemanticsProperties.Heading in c) parts += "heading"
        if (SemanticsProperties.Disabled in c) parts += "disabled"
        val actions = listOf(
            SemanticsActions.OnClick to "click", SemanticsActions.SetProgress to "setProgress",
            SemanticsActions.SetText to "setText", SemanticsActions.OnLongClick to "longClick",
        ).filter { it.first in c }.map { it.second }
        if (actions.isNotEmpty()) parts += "actions=" + actions.joinToString(",")
        if (parts.isEmpty()) null else parts.joinToString(" ")
    }.sorted().joinToString("\n", postfix = "\n") // compared as a set: the order is the theme's layout

    companion object {
        private const val MAX_CLICKS = 12
        private val ID = Regex("[a-z][a-z0-9-]{0,39}")
        private val THEMES = mapOf("material3" to Material3Theme, "minimal" to MinimalTheme, "skeuomorphic" to SkeuomorphicTheme)
        private val PILOT_TEMPLATES = listOf(
            "app.rose.template.feed.FeedTemplate",
            "app.rose.template.spritegame.SpriteGameTemplate",
            "app.rose.template.player.PlayerTemplate",
        )

        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "{0} {1}")
        fun cases(): List<Array<Any>> {
            val templates = System.getProperty("rose.evidence.templates")?.split(',')?.map { it.trim() } ?: PILOT_TEMPLATES
            return templates.flatMap { t -> THEMES.keys.map { h -> arrayOf<Any>(t, h) } }
        }
    }
}
