package app.rose.design.testing

import androidx.compose.foundation.layout.Column
import androidx.annotation.RequiresApi
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import app.rose.design.Divider
import org.robolectric.annotation.GraphicsMode
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.SemanticsNodeInteraction
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.assertHasClickAction
import androidx.compose.ui.test.assertHasNoClickAction
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertIsNotSelected
import androidx.compose.ui.test.assertIsOff
import androidx.compose.ui.test.assertIsOn
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.hasAnyDescendant
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.isHeading
import androidx.compose.ui.test.isToggleable
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performImeAction
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeRight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.isSpecified
import app.rose.design.BarAction
import app.rose.design.Button
import app.rose.design.ButtonKind
import app.rose.design.Direction
import app.rose.design.DirectionPad
import app.rose.design.Divider
import app.rose.design.HoldButton
import app.rose.design.Icon
import app.rose.design.IconButton
import app.rose.design.ListItem
import app.rose.design.Panel
import app.rose.design.PanelKind
import app.rose.design.Progress
import app.rose.design.RoseIcon
import app.rose.design.RoseTheme
import app.rose.design.Screen
import app.rose.design.Segmented
import app.rose.design.Slider
import app.rose.design.Stat
import app.rose.design.Text
import app.rose.design.TextField
import app.rose.design.Theme
import app.rose.design.Toggle
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test

/**
 * What every theme must keep, checked the same way for each theme in each mode. A theme module runs it with:
 *
 * ```
 * @RunWith(ParameterizedRobolectricTestRunner::class)
 * @Config(sdk = [34, 37])
 * class MinimalConformance(dark: Boolean) : ThemeConformanceTest(MinimalTheme, dark) {
 *     companion object { @JvmStatic @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}") fun modes() = ThemeCases.modes() }
 * }
 * ```
 *
 * Every check drives a control the way a template and a person do (taps, drags, holds, typing, accessibility
 * actions) and asserts the callbacks and semantics the Controls contract documents. Appearance is the theme's own
 * business; this suite never looks at a pixel.
 */
abstract class ThemeConformanceTest(private val theme: Theme, private val dark: Boolean) {

    @get:Rule
    val rule = createComposeRule()

    @Before
    fun skipPlaceholder() = assumeTrue("${theme.id} is a placeholder (implemented = false)", theme.implemented)

    private fun show(content: @Composable () -> Unit) = rule.setContent { RoseTheme(theme, dark) { content() } }

    private fun role(r: Role) = SemanticsMatcher.expectValue(SemanticsProperties.Role, r)
    private fun SemanticsNodeInteraction.assertTarget() = assertTouchWidthIsAtLeast(48.dp).assertTouchHeightIsAtLeast(48.dp)

    // The touch target: the node's bounds plus any minimum-touch-target expansion, in dp.
    private fun SemanticsNodeInteraction.touch() = fetchSemanticsNode().touchBoundsInRoot.let { b ->
        with(rule.density) { b.width.toDp() to b.height.toDp() }
    }
    private fun SemanticsNodeInteraction.assertTouchWidthIsAtLeast(min: Dp) = apply {
        val w = touch().first
        assertTrue("touch target width $w is at least $min", w + 0.5.dp >= min)
    }
    private fun SemanticsNodeInteraction.assertTouchHeightIsAtLeast(min: Dp) = apply {
        val h = touch().second
        assertTrue("touch target height $h is at least $min", h + 0.5.dp >= min)
    }
    /** [text] is on this element or inside it (a non-interactive container does not merge its children). */
    private fun SemanticsNodeInteraction.assertShows(text: String) = assert(hasText(text) or hasAnyDescendant(hasText(text)))
    private fun SemanticsNodeInteraction.progress(): ProgressBarRangeInfo =
        fetchSemanticsNode().config[SemanticsProperties.ProgressBarRangeInfo]

    @Test
    fun tokensAreCompleteAndReadable() {
        val t = theme.tokens(dark)
        val c = t.colors
        assertEquals("isDark matches the mode asked for", dark, c.isDark)
        assertTrue("minTouchTarget is at least 48dp", t.minTouchTarget >= 48.dp)
        Contrast.require("onBackground on background", c.onBackground, c.background, 4.5)
        Contrast.require("onSurface on surface", c.onSurface, c.surface, 4.5)
        Contrast.require("onSurfaceVariant on surfaceVariant", c.onSurfaceVariant, c.surfaceVariant, 4.5)
        Contrast.require("onAccent on accent", c.onAccent, c.accent, 4.5)
        Contrast.require("onDanger on danger", c.onDanger, c.danger, 4.5)
        Contrast.require("muted on surface", c.muted, c.surface, 3.0)
        Contrast.require("muted on background", c.muted, c.background, 3.0)
        Contrast.require("accent against surface (selection, progress)", c.accent, c.surface, 3.0)
        Contrast.require("accent against background", c.accent, c.background, 3.0)
        Contrast.require("danger against surface", c.danger, c.surface, 3.0)
        listOf(t.type.display, t.type.title, t.type.body, t.type.label, t.type.caption).forEach {
            assertTrue("every text role sets a font size", it.fontSize.isSpecified)
        }
    }

    @Test
    fun buttonClicksOnceHasRoleAndTarget() {
        val clicks = IntArray(ButtonKind.entries.size)
        var disabledClicks = 0
        show {
            Column {
                ButtonKind.entries.forEach { k ->
                    Button("Do ${k.name}", { clicks[k.ordinal]++ }, Modifier.testTag("b${k.ordinal}"), kind = k, icon = RoseIcon.Check.takeIf { k == ButtonKind.Primary })
                }
                Button("Never", { disabledClicks++ }, Modifier.testTag("off"), enabled = false)
            }
        }
        ButtonKind.entries.forEach { k ->
            rule.onNodeWithTag("b${k.ordinal}").assert(hasText("Do ${k.name}")).assert(role(Role.Button)).assertHasClickAction()
                .assertIsEnabled().assertTarget().performClick()
            assertEquals("$k button: one click is one onClick", 1, clicks[k.ordinal])
        }
        rule.onNodeWithTag("off").assertIsNotEnabled().performClick()
        assertEquals("a disabled button never calls onClick", 0, disabledClicks)
    }

    @Test
    fun iconButtonPlainAndToggle() {
        var plain = 0
        var on by mutableStateOf(false)
        var big = 0
        show {
            Column {
                IconButton(RoseIcon.Share, "Share", { plain++ }, Modifier.testTag("plain"))
                IconButton(RoseIcon.Loop, "Loop", { on = !on }, Modifier.testTag("toggle"), checked = on)
                IconButton(RoseIcon.Play, "Play", { big++ }, Modifier.testTag("big"), prominent = true)
                IconButton(RoseIcon.Close, "Closed", {}, Modifier.testTag("off"), enabled = false)
            }
        }
        rule.onNodeWithTag("plain").assert(hasContentDescription("Share")).assert(role(Role.Button)).assertTarget().performClick()
        assertEquals(1, plain)
        rule.onNodeWithTag("toggle").assert(hasContentDescription("Loop")).assert(isToggleable()).assertIsOff().assertTarget().performClick()
        rule.onNodeWithTag("toggle").assertIsOn()
        rule.onNodeWithTag("big").assert(hasContentDescription("Play")).assertTarget().performClick()
        assertEquals(1, big)
        rule.onNodeWithTag("off").assertIsNotEnabled()
    }

    @Test
    fun toggleIsOneSwitchWithItsLabel() {
        var checked by mutableStateOf(false)
        var disabledChanges = 0
        show {
            Column {
                Toggle(checked, { checked = it }, "Shuffle", Modifier.testTag("t"))
                Toggle(false, { disabledChanges++ }, "Locked", Modifier.testTag("off"), enabled = false)
            }
        }
        rule.onNodeWithTag("t").assert(isToggleable()).assert(role(Role.Switch)).assertTextContains("Shuffle").assertIsOff()
            .assertTouchHeightIsAtLeast(48.dp).performClick()
        assertTrue(checked)
        rule.onNodeWithTag("t").assertIsOn().performClick()
        assertTrue(!checked)
        rule.onNodeWithTag("off").assertIsNotEnabled().performClick()
        assertEquals(0, disabledChanges)
    }

    @Test
    fun sliderReportsRangeAndFollowsTapsDragsAndAccessibility() {
        var value by mutableFloatStateOf(0.2f)
        var finished = 0
        show {
            Column {
                Slider(
                    value, { value = it }, "Position", Modifier.testTag("s").width(320.dp),
                    onValueChangeFinished = { finished++ }, valueText = "Somewhere", highlight = 0.3f..0.6f, marks = listOf(0.3f, 0.6f),
                )
            }
        }
        val s = rule.onNodeWithTag("s").assert(hasContentDescription("Position")).assertTouchHeightIsAtLeast(48.dp)
        assertEquals(0.2f, s.progress().current, 0.001f)
        assertEquals(0f..1f, s.progress().range)
        assertEquals("Somewhere", s.fetchSemanticsNode().config[SemanticsProperties.StateDescription])

        s.performSemanticsAction(SemanticsActions.SetProgress) { it(0.75f) }
        assertEquals("set-progress sets the value", 0.75f, value, 0.01f)
        assertTrue("set-progress finishes the change", finished >= 1)

        val before = finished
        s.performTouchInput { down(centerLeft.copy(x = 1f)); up() }
        assertTrue("a tap at the start moves the value to the start (was $value)", value <= 0.1f)
        s.performTouchInput { down(centerRight.copy(x = width - 1f)); up() }
        assertTrue("a tap at the end moves the value to the end (was $value)", value >= 0.9f)
        s.performTouchInput { down(centerLeft.copy(x = 1f)); up() }
        s.performTouchInput { swipeRight() }
        assertTrue("a drag to the end moves the value to the end (was $value)", value >= 0.9f)
        assertTrue("every gesture ends with onValueChangeFinished", finished >= before + 4)
        assertTrue("values stay inside the range", value in 0f..1f)
    }

    @Test
    fun steppedAndDisabledSliders() {
        var stepped by mutableFloatStateOf(0f)
        var disabledChanges = 0
        show {
            Column {
                Slider(stepped, { stepped = it }, "Steps", Modifier.testTag("s").width(320.dp), valueRange = 0f..10f, steps = 4)
                Slider(0.5f, { disabledChanges++ }, "Off", Modifier.testTag("off").width(320.dp), enabled = false)
            }
        }
        rule.onNodeWithTag("s").performSemanticsAction(SemanticsActions.SetProgress) { it(3.3f) }
        assertEquals("steps = 4 over 0..10 allows 0, 2, 4, 6, 8, 10", 4f, stepped, 0.001f)
        rule.onNodeWithTag("s").performTouchInput { down(center); up() }
        assertTrue("a tap snaps to a step (was $stepped)", listOf(0f, 2f, 4f, 6f, 8f, 10f).any { kotlin.math.abs(it - stepped) < 0.001f })
        val off = rule.onNodeWithTag("off").assertIsNotEnabled()
        off.performTouchInput { down(centerRight.copy(x = width - 1f)); up() }
        assertEquals("a disabled slider never changes", 0, disabledChanges)
    }

    @Test
    fun progressDeterminateAndIndeterminate() {
        show {
            Column {
                Progress(0.4f, "Upload", Modifier.testTag("p"))
                Progress(null, "Loading", Modifier.testTag("i"))
            }
        }
        rule.onNodeWithTag("p").assert(hasContentDescription("Upload")).assertHasNoClickAction()
        assertEquals(0.4f, rule.onNodeWithTag("p").progress().current, 0.001f)
        rule.onNodeWithTag("i").assert(hasContentDescription("Loading"))
        assertEquals(ProgressBarRangeInfo.Indeterminate, rule.onNodeWithTag("i").progress())
    }

    @Test
    fun listItemRowAndTrailingControlAreSeparate() {
        var row = 0
        var trailing = 0
        show {
            Column {
                ListItem(
                    "Headline", Modifier.testTag("row"), supporting = "Supporting", leadingIcon = RoseIcon.Person,
                    trailing = { IconButton(RoseIcon.More, "More", { trailing++ }) }, onClick = { row++ }, selected = true,
                )
                ListItem("Static", Modifier.testTag("static"))
            }
        }
        rule.onNodeWithTag("row").assertTextContains("Headline").assertTextContains("Supporting").assertIsSelected()
            .assertHasClickAction().assertTouchHeightIsAtLeast(48.dp).performClick()
        assertEquals(1, row)
        rule.onNodeWithContentDescription("More").performClick()
        assertEquals("the trailing control is its own target", 1, trailing)
        assertEquals("…and does not click the row", 1, row)
        rule.onNodeWithTag("static").assertShows("Static").assertHasNoClickAction()
    }

    @Test
    fun segmentedSelectsOneOption() {
        var selected by mutableIntStateOf(0)
        show { Segmented(listOf("Day", "Week", "Month"), selected, { selected = it }, "Range", Modifier.testTag("seg")) }
        rule.onNodeWithText("Day").assertIsSelected().assert(role(Role.Tab)).assertTouchHeightIsAtLeast(48.dp)
        rule.onNodeWithText("Week").assertIsNotSelected().performClick()
        assertEquals(1, selected)
        rule.onNodeWithText("Week").assertIsSelected()
        rule.onNodeWithText("Day").assertIsNotSelected()
    }

    @Test
    fun textFieldTypesAndSubmits() {
        var text by mutableStateOf("")
        var submitted = 0
        show { TextField(text, { text = it }, "Title", Modifier.testTag("f"), placeholder = "Say something", onSubmit = { submitted++ }) }
        val field = rule.onNode(hasTestTag("f") and hasSetTextAction())
        field.performTextInput("Hello")
        assertEquals("Hello", text)
        field.performImeAction()
        assertEquals(1, submitted)
    }

    @Test
    fun everyIconDrawsWithItsDescription() {
        show { Column { RoseIcon.entries.forEach { Icon(it, "icon ${it.name}") } } }
        RoseIcon.entries.forEach { rule.onNodeWithContentDescription("icon ${it.name}").assertExists() }
    }

    @Test
    fun directionPadHoldsAndReleasesEachDirection() {
        val events = mutableListOf<Pair<Direction, Boolean>>()
        show { DirectionPad({ d, down -> events += d to down }, Modifier.testTag("pad")) }
        Direction.entries.forEach { d ->
            val key = rule.onNodeWithContentDescription(d.label).assert(role(Role.Button)).assertTarget()
            events.clear()
            key.performTouchInput { down(center) }
            assertEquals("touching $d down holds it", listOf(d to true), events)
            key.performTouchInput { up() }
            assertEquals("lifting releases $d", listOf(d to true, d to false), events)
            events.clear()
            key.performClick()
            assertEquals("a click (or accessibility click) is a press then a release", listOf(d to true, d to false), events)
        }
    }

    @Test
    fun directionPadSubsetAndHoldButtonWorkTogether() {
        val held = mutableSetOf<String>()
        show {
            Column {
                DirectionPad({ d, down -> if (down) held += d.label else held -= d.label }, directions = setOf(Direction.Left, Direction.Right))
                HoldButton("Jump", { down -> if (down) held += "Jump" else held -= "Jump" }, Modifier.testTag("jump"), icon = RoseIcon.Jump)
            }
        }
        rule.onNodeWithContentDescription("Up").assertDoesNotExist()
        rule.onNodeWithTag("jump").assert(hasContentDescription("Jump")).assert(role(Role.Button)).assertTarget()
        val left = rule.onNodeWithContentDescription("Left")
        val jump = rule.onNodeWithContentDescription("Jump")
        left.performTouchInput { down(0, center) }
        jump.performTouchInput { down(1, center) }
        assertEquals("one finger holds Left while another holds Jump", setOf("Left", "Jump"), held)
        jump.performTouchInput { up(1) }
        assertEquals("releasing Jump leaves Left held", setOf("Left"), held)
        left.performTouchInput { up(0) }
        assertEquals(emptySet<String>(), held)
    }

    @Test
    fun statShowsLabelValueAndOptionalProgress() {
        show {
            Column {
                Stat("Score", "1200", Modifier.testTag("score"))
                Stat("Health", "3/5", Modifier.testTag("hp"), progress = 0.6f)
            }
        }
        rule.onNodeWithTag("score").assertTextContains("Score").assertTextContains("1200").assertHasNoClickAction()
        rule.onNodeWithTag("hp").assertTextContains("Health").assertTextContains("3/5")
        assertEquals(0.6f, rule.onNodeWithTag("hp").progress().current, 0.001f)
    }

    @Test
    fun screenShowsTitleBarsAndContent() {
        var back = 0
        var loop by mutableStateOf(false)
        show {
            Screen(
                "Library", Modifier.testTag("screen"),
                navigation = BarAction(RoseIcon.Back, "Back", { back++ }),
                actions = listOf(BarAction(RoseIcon.Loop, "Loop", { loop = !loop }, checked = loop)),
                bottomBar = { Text("Bottom bar") },
            ) { padding -> Text("Body", Modifier.padding(padding)) }
        }
        rule.onNode(hasText("Library") and isHeading()).assertIsDisplayed()
        rule.onNodeWithContentDescription("Back").assertTarget().performClick()
        assertEquals(1, back)
        rule.onNodeWithContentDescription("Loop").assert(isToggleable()).assertIsOff().performClick()
        rule.onNodeWithContentDescription("Loop").assertIsOn()
        rule.onNodeWithText("Body").assertIsDisplayed()
        rule.onNodeWithText("Bottom bar").assertIsDisplayed()
    }

    @Test
    fun panelsClickAsOneAndCarryContent() {
        var opened = 0
        show {
            Column {
                Panel(Modifier.testTag("card"), onClick = { opened++ }, onClickLabel = "Open") { Text("A post") }
                PanelKind.entries.forEach { k -> Panel(Modifier.testTag("p$k"), kind = k) { Text("Kind $k") } }
                Divider(Modifier.testTag("div"))
            }
        }
        rule.onNodeWithTag("card").assertTextContains("A post").assert(role(Role.Button)).performClick()
        assertEquals(1, opened)
        PanelKind.entries.forEach { rule.onNodeWithTag("p$it").assertShows("Kind $it").assertHasNoClickAction() }
        rule.onNodeWithTag("div").assertExists()
    }

    // Size and visibility (pixels): a control draws at the size its modifier gives it and stays visible on every
    // surface a template may put it on: background, surface, and an Inset panel's surfaceVariant.

    @Test
    fun controlsTakeTheSizeTheyAreGiven() {
        show {
            Column {
                Button("Sized", {}, Modifier.testTag("Button").size(240.dp, 72.dp))
                Segmented(listOf("One", "Two"), 0, {}, "Sized", Modifier.testTag("Segmented").size(240.dp, 72.dp))
                Slider(0.5f, {}, "Sized", Modifier.testTag("Slider").size(240.dp, 72.dp))
                Toggle(false, {}, "Sized", Modifier.testTag("Toggle").size(240.dp, 72.dp))
                Progress(0.5f, "Sized", Modifier.testTag("Progress").size(240.dp, 24.dp))
                Divider(Modifier.testTag("Divider").size(240.dp, 8.dp))
            }
        }
        listOf("Button" to 72.dp, "Segmented" to 72.dp, "Slider" to 72.dp, "Toggle" to 72.dp, "Progress" to 24.dp, "Divider" to 8.dp).forEach { (tag, h) ->
            val b = rule.onNodeWithTag(tag).getUnclippedBoundsInRoot()
            assertEquals("$tag given 240dp wide keeps that width", 240f, (b.right - b.left).value, 1f)
            // At least: Material widens some controls' accessibility bounds beyond what they draw (pixels check the drawing).
            assertTrue("$tag given $h tall is at least that tall, was ${b.bottom - b.top}", (b.bottom - b.top).value >= h.value - 1f)
        }
    }

    @Test
    @GraphicsMode(GraphicsMode.Mode.NATIVE)
    @RequiresApi(26) // captureToImage; these run on API 34 and 37
    fun controlsDrawAcrossTheHeightTheyAreGiven() {
        val host = theme.tokens(dark).colors.background
        show {
            Box(Modifier.background(host)) {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Progress(0.5f, "Tall", Modifier.testTag("Progress").size(240.dp, 24.dp))
                    Segmented(listOf("One", "Two"), 0, {}, "Tall", Modifier.testTag("Segmented").size(240.dp, 72.dp))
                    Button("Tall", {}, Modifier.testTag("Button").size(240.dp, 72.dp), kind = ButtonKind.Primary)
                }
            }
        }
        listOf("Progress" to 24, "Segmented" to 72, "Button" to 72).forEach { (tag, h) ->
            Pixels.requireSpan("$tag given ${h}dp (${mode()})", rule.onNodeWithTag(tag).captureToImage(), host, 0.5f)
        }
    }

    @Test
    fun aTallSliderTakesInputAcrossItsWholeHeight() {
        var value by mutableFloatStateOf(0.5f)
        show { Slider(value, { value = it }, "Tall", Modifier.testTag("s").size(320.dp, 96.dp)) }
        rule.onNodeWithTag("s").performTouchInput { down(Offset(width - 2f, 3f)); up() }
        assertTrue("a tap at the top edge, at the end, moves the value to the end (was $value)", value >= 0.9f)
        rule.onNodeWithTag("s").performTouchInput { down(Offset(2f, height - 3f)); up() }
        assertTrue("a tap at the bottom edge, at the start, moves the value to the start (was $value)", value <= 0.1f)
    }

    @Test
    @GraphicsMode(GraphicsMode.Mode.NATIVE)
    @RequiresApi(26) // captureToImage; these run on API 34 and 37
    fun tracksAndEdgesStayVisibleOnEverySurface() {
        val c = theme.tokens(dark).colors
        val hosts = listOf("background" to c.background, "surface" to c.surface, "surfaceVariant (an Inset panel)" to c.surfaceVariant)
        var host by mutableIntStateOf(0)
        show {
            Box(Modifier.background(hosts[host].second).padding(16.dp)) {
                Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
                    Progress(0f, "Empty", Modifier.testTag("Progress").width(240.dp))
                    Slider(0f, {}, "At start", Modifier.testTag("Slider").width(240.dp))
                    Button("Secondary", {}, Modifier.testTag("Button").width(200.dp))
                }
            }
        }
        hosts.forEachIndexed { i, (name, color) ->
            host = i
            rule.waitForIdle()
            val on = "on $name (${mode()})"
            Pixels.requireVisible("Progress(0f): its empty track $on", rule.onNodeWithTag("Progress").captureToImage(), color, Pixels.Region(x0 = 0.5f))
            Pixels.requireVisible("Slider at 0: its inactive track $on", rule.onNodeWithTag("Slider").captureToImage(), color, Pixels.Region(x0 = 0.6f))
            Pixels.requireVisible(
                "Secondary Button: its edge or fill $on", rule.onNodeWithTag("Button").captureToImage(), color,
                Pixels.Region(x1 = 0.06f, y0 = 0.25f, y1 = 0.75f),
            )
        }
    }

    private fun mode() = if (dark) "dark" else "light"
}
