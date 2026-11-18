package app.rose.template.feed

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import app.rose.design.BarAction
import app.rose.design.Button
import app.rose.design.ButtonKind
import app.rose.design.Divider
import app.rose.design.Icon
import app.rose.design.Panel
import app.rose.design.RoseIcon
import app.rose.design.RoseTheme
import app.rose.design.Screen
import app.rose.design.Template
import app.rose.design.Text
import app.rose.design.TextRole

/** A feed with local sample posts and saveable reading state. */
object FeedTemplate : Template {
    override val id = "feed"
    override val displayName = "Feed"
    override val implemented = true

    @Composable
    override fun App(modifier: Modifier) {
        // Keep the list state at the navigation level so its index and pixel offset survive a trip to detail.
        val listState = rememberLazyListState()
        var openPostId by rememberSaveable { mutableStateOf<Int?>(null) }
        var savedPostIds by rememberSaveable { mutableStateOf(intArrayOf()) }
        val post = samplePosts.firstOrNull { it.id == openPostId }

        if (post == null) {
            FeedList(listState, savedPostIds, onOpen = { openPostId = it }, modifier = modifier)
        } else {
            val back = { openPostId = null }
            BackHandler(onBack = back)
            PostDetail(
                post = post,
                saved = post.id in savedPostIds,
                onBack = back,
                onToggleSaved = {
                    savedPostIds = if (post.id in savedPostIds) {
                        savedPostIds.filterNot { it == post.id }.toIntArray()
                    } else {
                        savedPostIds + post.id
                    }
                },
                modifier = modifier,
            )
        }
    }
}

@Composable
private fun FeedList(
    state: LazyListState,
    savedPostIds: IntArray,
    onOpen: (Int) -> Unit,
    modifier: Modifier,
) {
    val spacing = RoseTheme.spacing
    Screen("Feed", modifier) { insets ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(insets),
            state = state,
            contentPadding = PaddingValues(start = spacing.l, top = spacing.l, end = spacing.l, bottom = spacing.xl),
            verticalArrangement = Arrangement.spacedBy(spacing.m),
        ) {
            items(samplePosts, key = { it.id }) { post ->
                Panel(
                    modifier = Modifier.fillMaxWidth(),
                    onClick = { onOpen(post.id) },
                    onClickLabel = "Read post",
                ) {
                    Column(verticalArrangement = Arrangement.spacedBy(spacing.s)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(spacing.s),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(post.topic, Modifier.weight(1f), role = TextRole.Label)
                            // Reserve the space in both states so saving a post cannot shift the list.
                            Box(Modifier.size(spacing.l), contentAlignment = Alignment.Center) {
                                if (post.id in savedPostIds) {
                                    Icon(RoseIcon.Check, "Saved", size = spacing.l)
                                }
                            }
                        }
                        Text(post.title, role = TextRole.Title)
                        Text(post.summary)
                        Text(post.byline, role = TextRole.Caption, color = RoseTheme.colors.muted)
                    }
                }
            }
        }
    }
}

@Composable
private fun PostDetail(
    post: FeedPost,
    saved: Boolean,
    onBack: () -> Unit,
    onToggleSaved: () -> Unit,
    modifier: Modifier,
) {
    val spacing = RoseTheme.spacing
    Screen("Post", modifier, navigation = BarAction(RoseIcon.Back, "Back to feed", onBack)) { insets ->
        Column(
            modifier = Modifier.fillMaxSize().padding(insets).verticalScroll(rememberScrollState())
                .padding(start = spacing.xl, top = spacing.xl, end = spacing.xl, bottom = spacing.xxl),
            verticalArrangement = Arrangement.spacedBy(spacing.l),
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(spacing.s)) {
                Text(post.topic, role = TextRole.Label)
                Text(post.title, Modifier.semantics { heading() }, role = TextRole.Display)
                Text(post.byline, role = TextRole.Caption, color = RoseTheme.colors.muted)
            }
            Text(post.summary)
            Button(
                label = if (saved) "Remove from saved" else "Save post",
                onClick = onToggleSaved,
                kind = if (saved) ButtonKind.Secondary else ButtonKind.Primary,
                icon = if (saved) RoseIcon.Remove else RoseIcon.Add,
            )
            Divider()
            Text(post.body)
        }
    }
}
