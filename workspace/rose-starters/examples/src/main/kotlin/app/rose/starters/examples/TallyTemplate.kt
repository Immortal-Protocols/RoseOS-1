package app.rose.starters.examples

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.calculateEndPadding
import androidx.compose.foundation.layout.calculateStartPadding
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import app.rose.design.BarAction
import app.rose.design.Button
import app.rose.design.ButtonKind
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
import app.rose.design.Template
import app.rose.design.Text
import app.rose.design.TextField
import app.rose.design.TextRole
import app.rose.design.Toggle

/**
 * The example template: count things toward a goal. Small on purpose, but shaped like a pilot template: it owns all
 * behaviour and state ([TallyState]), draws only with `app.rose.design` (stock controls, Text, foundation layout and
 * tokens), never names a theme, and has two screens with navigation. Not one of the pilot templates.
 */
object TallyTemplate : Template {
    override val id = "tally"
    override val displayName = "Tally"

    @Composable
    override fun App(modifier: Modifier) {
        TallyScreen(rememberTallyState(), modifier)
    }
}

/** One counter. Immutable: the state replaces it on change. */
data class Counter(val id: Int, val name: String, val count: Int)

/** All of Tally's behaviour. Plain state and rules, no appearance; survives configuration changes. */
@Stable
class TallyState(counters: List<Counter>, goal: Int, step: Int, allowNegative: Boolean, openId: Int?) {
    val counters = mutableStateListOf<Counter>().apply { addAll(counters) }
    var goal by mutableIntStateOf(goal)
    var step by mutableIntStateOf(step)
    var allowNegative by mutableStateOf(allowNegative)
    var openId by mutableStateOf(openId)
    var draft by mutableStateOf("")

    val total get() = counters.sumOf { it.count }
    val open get() = counters.firstOrNull { it.id == openId }

    fun change(id: Int, by: Int) {
        val i = counters.indexOfFirst { it.id == id }.takeIf { it >= 0 } ?: return
        val next = counters[i].count + by
        counters[i] = counters[i].copy(count = if (allowNegative) next else maxOf(0, next))
    }

    fun add(): Boolean {
        val name = draft.trim()
        if (name.isEmpty() || counters.any { it.name.equals(name, ignoreCase = true) }) return false
        counters += Counter((counters.maxOfOrNull { it.id } ?: 0) + 1, name, 0)
        draft = ""
        return true
    }

    fun resetAll() {
        for (i in counters.indices) counters[i] = counters[i].copy(count = 0)
    }

    companion object {
        val Steps = listOf(1, 2, 5)

        val Saver = listSaver<TallyState, Any?>(
            save = { s -> listOf(s.counters.flatMap { listOf(it.id, it.name, it.count) }, s.goal, s.step, s.allowNegative, s.openId, s.draft) },
            restore = { v ->
                @Suppress("UNCHECKED_CAST")
                val flat = v[0] as List<Any>
                TallyState(flat.chunked(3).map { Counter(it[0] as Int, it[1] as String, it[2] as Int) }, v[1] as Int, v[2] as Int, v[3] as Boolean, v[4] as Int?)
                    .also { it.draft = v[5] as String }
            },
        )

        fun sample() = TallyState(listOf(Counter(1, "Apples", 0), Counter(2, "Pears", 0), Counter(3, "Glasses of water", 0)), goal = 10, step = 1, allowNegative = false, openId = null)
    }
}

@Composable
fun rememberTallyState(): TallyState = rememberSaveable(saver = TallyState.Saver) { TallyState.sample() }

/** Tally's two screens: the list, and one counter. */
@Composable
fun TallyScreen(state: TallyState, modifier: Modifier = Modifier) {
    val open = state.open
    if (open == null) TallyList(state, modifier) else TallyDetail(state, open, modifier)
}

@Composable
private fun TallyList(state: TallyState, modifier: Modifier) {
    val space = RoseTheme.spacing
    val direction = LocalLayoutDirection.current
    Screen(
        title = "Tally",
        modifier = modifier,
        actions = listOf(BarAction(RoseIcon.Restart, "Reset all", state::resetAll)),
    ) { padding ->
        LazyColumn(
            Modifier.fillMaxSize(),
            contentPadding = PaddingValues(
                start = space.l + padding.calculateStartPadding(direction),
                end = space.l + padding.calculateEndPadding(direction),
                top = space.l, bottom = space.l + padding.calculateBottomPadding(),
            ),
            verticalArrangement = Arrangement.spacedBy(space.m),
        ) {
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(space.xl)) {
                    Stat("Total", state.total.toString())
                    Stat("Goal each", state.goal.toString())
                    Stat("Reached", "${state.counters.count { it.count >= state.goal }} of ${state.counters.size}")
                }
            }
            item {
                Panel(kind = PanelKind.Flat) {
                    Column(verticalArrangement = Arrangement.spacedBy(space.s)) {
                        Text("Step", role = TextRole.Label)
                        Segmented(TallyState.Steps.map { "+$it" }, TallyState.Steps.indexOf(state.step), { state.step = TallyState.Steps[it] }, "Step")
                        Text("Goal: ${state.goal}", role = TextRole.Label)
                        Slider(
                            state.goal.toFloat(), { state.goal = it.toInt() }, "Goal", valueRange = 5f..50f, steps = 8,
                            valueText = "${state.goal} each",
                        )
                        Toggle(state.allowNegative, { state.allowNegative = it }, "Allow counts below zero")
                    }
                }
            }
            items(state.counters, key = { it.id }) { c ->
                Panel(kind = PanelKind.Raised) {
                    Column {
                        ListItem(
                            headline = c.name,
                            supporting = "${c.count} of ${state.goal}",
                            onClick = { state.openId = c.id },
                            trailing = {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    IconButton(RoseIcon.Remove, "Decrease ${c.name}", { state.change(c.id, -state.step) })
                                    Text(c.count.toString(), Modifier.widthIn(min = 32.dp), role = TextRole.Title, textAlign = TextAlign.Center)
                                    IconButton(RoseIcon.Add, "Increase ${c.name}", { state.change(c.id, state.step) })
                                }
                            },
                        )
                        Progress((c.count.toFloat() / state.goal).coerceIn(0f, 1f), "${c.name} progress", Modifier.padding(top = space.s))
                    }
                }
            }
            item {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(space.s)) {
                    TextField(state.draft, { state.draft = it }, "New counter", Modifier.weight(1f), placeholder = "Name", onSubmit = { state.add() })
                    Button("Add", { state.add() }, kind = ButtonKind.Primary, icon = RoseIcon.Add, enabled = state.draft.isNotBlank())
                }
            }
        }
    }
}

@Composable
private fun TallyDetail(state: TallyState, c: Counter, modifier: Modifier) {
    val space = RoseTheme.spacing
    BackHandler { state.openId = null }
    Screen(title = c.name, modifier = modifier, navigation = BarAction(RoseIcon.Back, "Back", { state.openId = null })) { padding ->
        Column(
            Modifier.fillMaxSize().padding(padding).padding(space.l),
            verticalArrangement = Arrangement.spacedBy(space.l),
        ) {
            Panel(Modifier.fillMaxWidth(), kind = PanelKind.Inset) {
                Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxWidth()) {
                    Text(c.count.toString(), role = TextRole.Display)
                    Text(if (c.count >= state.goal) "Goal reached" else "${state.goal - c.count} to go", color = RoseTheme.colors.muted)
                }
            }
            Progress((c.count.toFloat() / state.goal).coerceIn(0f, 1f), "${c.name} progress")
            Row(horizontalArrangement = Arrangement.spacedBy(space.m)) {
                Button("Add ${state.step}", { state.change(c.id, state.step) }, Modifier.weight(1f), kind = ButtonKind.Primary)
                Button("Take ${state.step}", { state.change(c.id, -state.step) }, Modifier.weight(1f))
            }
            Button("Reset ${c.name}", { state.change(c.id, -c.count) }, kind = ButtonKind.Danger, enabled = c.count != 0)
        }
    }
}
