package dev.rose.music.ui

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.CloudDownload
import androidx.compose.material.icons.rounded.DownloadDone
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material.icons.rounded.Shuffle
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.zIndex
import dev.rose.music.Catalog
import dev.rose.music.DownloadState
import dev.rose.music.LibraryStatus
import dev.rose.music.PlayerUi
import dev.rose.music.Track
import dev.rose.music.formatBytes
import dev.rose.music.formatDuration
import dev.rose.music.formatTotal
import dev.rose.music.rememberCover
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.abs

class LibraryActions(
    val play: (Track) -> Unit,
    val playAll: (shuffle: Boolean) -> Unit,
    val download: (Track) -> Unit,
    val downloadAll: () -> Unit,
    val refresh: suspend () -> Unit,
    val canPlay: (Track) -> Boolean,
    val remove: (Track) -> Unit,
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LibraryScreen(
    catalog: Catalog?,
    status: LibraryStatus,
    downloads: Map<String, DownloadState>,
    player: PlayerUi,
    actions: LibraryActions,
    contentPadding: PaddingValues,
    modifier: Modifier = Modifier,
) {
    val colors = LocalCoverColors.current
    val list = rememberLazyListState()
    val scope = rememberCoroutineScope()
    var refreshing by remember { mutableStateOf(false) }
    var savedOnly by rememberSaveable { mutableStateOf(false) }
    val haptics = rememberHaptics()
    val appeared = remember { mutableSetOf<String>() }

    PullToRefreshBox(
        isRefreshing = refreshing,
        onRefresh = { scope.launch { refreshing = true; haptics.tap(); actions.refresh(); refreshing = false } },
        modifier = modifier.fillMaxSize(),
    ) {
        if (catalog == null) {
            EmptyState(status, contentPadding)
            return@PullToRefreshBox
        }
        val saved = catalog.tracks.count { downloads[it.id] is DownloadState.Saved }
        val shown = if (savedOnly) catalog.tracks.filter { downloads[it.id] is DownloadState.Saved } else catalog.tracks
        LazyColumn(state = list, contentPadding = contentPadding, modifier = Modifier.fillMaxSize()) {
            item(key = "hero") {
                val parallax by remember { derivedStateOf { if (list.firstVisibleItemIndex == 0) list.firstVisibleItemScrollOffset.toFloat() else 2000f } }
                Column(
                    Modifier.fillMaxWidth().graphicsLayer {
                        translationY = parallax * 0.45f
                        alpha = (1f - parallax / 900f).coerceIn(0f, 1f)
                    }
                ) {
                    Gap(12.dp)
                    CoverFan(catalog.tracks.take(5), player.trackId, onTap = { haptics.tap(); actions.play(it) })
                    Gap(20.dp)
                    Column(Modifier.padding(horizontal = 24.dp)) {
                        Text("ALBUM · ${catalog.tracks.size} SONG${if (catalog.tracks.size == 1) "" else "S"}", style = Type.Eyebrow, color = colors.accent.copy(alpha = 0.9f))
                        Gap(8.dp)
                        Text(catalog.album.ifBlank { catalog.name }, style = Type.Hero, color = Ink.Text)
                        Gap(8.dp)
                        Text(
                            "${catalog.tracks.firstOrNull()?.artist ?: ""} · ${formatTotal(catalog.totalDurationMs)} · ${formatBytes(catalog.totalBytes)}",
                            style = Type.Meta, color = Ink.Muted,
                        )
                        Gap(14.dp)
                        StatusPill(status, saved)
                    }
                }
            }
            item(key = "actions") {
                ActionRow(catalog, downloads, colors.accent, actions, Modifier.padding(start = 24.dp, end = 24.dp, top = 22.dp, bottom = 8.dp))
            }
            item(key = "filter") {
                Segmented(
                    options = listOf("All songs", "Saved · $saved"),
                    selected = if (savedOnly) 1 else 0,
                    onSelect = { haptics.tick(); savedOnly = it == 1 },
                    modifier = Modifier.padding(horizontal = 24.dp, vertical = 12.dp),
                )
            }
            if (shown.isEmpty()) {
                item(key = "none") {
                    Text(
                        "Nothing saved yet. Tap the arrow on any song to keep it on this phone.",
                        style = Type.Meta, color = Ink.Muted, modifier = Modifier.padding(horizontal = 24.dp, vertical = 28.dp),
                    )
                }
            }
            items(shown, key = { it.id }) { t ->
                val first = remember { t.id !in appeared }
                val enter = remember { Animatable(if (first) 0f else 1f) }
                LaunchedEffect(Unit) {
                    if (first) {
                        appeared += t.id
                        delay(60L + 38L * shown.indexOf(t).coerceAtMost(12))
                        enter.animateTo(1f, spring(0.8f, 260f))
                    }
                }
                TrackRow(
                    track = t,
                    state = downloads[t.id] ?: DownloadState.None,
                    current = player.trackId == t.id,
                    playing = player.trackId == t.id && player.playing,
                    available = actions.canPlay(t),
                    onPlay = { haptics.tap(); actions.play(t) },
                    onDownload = { haptics.tap(); actions.download(t) },
                    onRemove = { haptics.heavy(); actions.remove(t) },
                    modifier = Modifier
                        .animateItem()
                        .graphicsLayer { alpha = enter.value; translationY = (1f - enter.value) * 48.dp.toPx() },
                )
            }
            item(key = "foot") {
                Text(
                    "Generated with ACE-Step 1.5 XL Turbo · hosted on a Chariot agent",
                    style = Type.Meta, color = Ink.Faint,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 28.dp),
                )
            }
        }
    }
}

@Composable
private fun EmptyState(status: LibraryStatus, padding: PaddingValues) {
    Column(
        Modifier.fillMaxSize().padding(padding).padding(32.dp),
        verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Spinner(LocalCoverColors.current.accent, Modifier.size(40.dp))
        Gap(20.dp)
        Text(
            when (status) {
                is LibraryStatus.Offline -> "You're offline"
                LibraryStatus.Waking -> "The library is waking up"
                else -> "Opening the library"
            },
            style = Type.Title, color = Ink.Text,
        )
        Gap(8.dp)
        Text(
            if (status is LibraryStatus.Offline || status == LibraryStatus.Waking) "Pull down to try again." else "Fetching songs from Chariot…",
            style = Type.Meta, color = Ink.Muted,
        )
    }
}

/** Five covers fanned like a hand of cards; they deal out on open and the playing one lifts. */
@Composable
private fun CoverFan(tracks: List<Track>, currentId: String?, onTap: (Track) -> Unit) {
    val open = remember { Animatable(0f) }
    LaunchedEffect(Unit) { delay(120); open.animateTo(1f, spring(0.62f, 120f)) }
    BoxWithConstraints(Modifier.fillMaxWidth().height(236.dp), contentAlignment = Alignment.Center) {
        val coverSize = minOf(176.dp, maxWidth * 0.42f)
        val spread = minOf(maxWidth * 0.16f, 92.dp)
        val mid = (tracks.size - 1) / 2f
        tracks.forEachIndexed { i, t ->
            val d = i - mid
            val lifted by animateFloatAsState(if (t.id == currentId) 1f else 0f, spring(0.55f, 300f), label = "lift")
            val cover by rememberCover(t.thumb ?: t.cover)
            val glow = cover?.colors?.glow ?: Color.Black
            Box(
                Modifier
                    .zIndex(10f - abs(d) + lifted * 10f)
                    .graphicsLayer {
                        val o = open.value
                        translationX = (d * spread.toPx()) * o
                        translationY = (abs(d) * 14.dp.toPx() - lifted * 18.dp.toPx()) * o
                        rotationZ = d * 7.5f * o
                        val s = (1f - abs(d) * 0.08f) * (0.85f + 0.15f * o) * (1f + 0.06f * lifted)
                        scaleX = s; scaleY = s
                    }
                    .shadow(24.dp, RoundedCornerShape(22.dp), ambientColor = glow, spotColor = glow)
                    .semantics { contentDescription = "Play ${t.title}" }
                    .pressable({ onTap(t) }, pressedScale = 0.93f)
            ) {
                Cover(t.cover, coverSize, 22.dp)
            }
        }
    }
}

@Composable
private fun ActionRow(catalog: Catalog, downloads: Map<String, DownloadState>, accent: Color, actions: LibraryActions, modifier: Modifier) {
    val haptics = rememberHaptics()
    Row(modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(
            Modifier.height(52.dp).clip(RoundedCornerShape(50)).background(accent)
                .pressable({ haptics.tap(); actions.playAll(false) })
                .padding(horizontal = 22.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(Icons.Rounded.PlayArrow, null, tint = Ink.Night, modifier = Modifier.size(26.dp))
            HGap(6.dp)
            Text("Play", style = Type.Label, color = Ink.Night)
        }
        Box(
            Modifier.size(52.dp).clip(CircleShape).background(Color.White.copy(alpha = 0.08f))
                .border(0.75.dp, Color.White.copy(alpha = 0.1f), CircleShape)
                .semantics { contentDescription = "Shuffle all" }
                .pressable({ haptics.tap(); actions.playAll(true) }),
            contentAlignment = Alignment.Center,
        ) { Icon(Icons.Rounded.Shuffle, null, tint = Ink.Text, modifier = Modifier.size(22.dp)) }
        Box(Modifier.weight(1f))
        DownloadAll(catalog, downloads, accent) { haptics.tap(); actions.downloadAll() }
    }
}

@Composable
private fun DownloadAll(catalog: Catalog, downloads: Map<String, DownloadState>, accent: Color, onClick: () -> Unit) {
    val total = catalog.totalBytes.coerceAtLeast(1)
    var have = 0L
    var running = 0
    catalog.tracks.forEach { t ->
        when (val s = downloads[t.id]) {
            is DownloadState.Saved -> have += t.bytes
            is DownloadState.Running -> { have += (t.bytes * s.progress).toLong(); running++ }
            is DownloadState.Queued -> running++
            else -> {}
        }
    }
    val all = catalog.tracks.all { downloads[it.id] is DownloadState.Saved }
    val frac by animateFloatAsState(have.toFloat() / total, spring(stiffness = 90f), label = "all")
    val bg by animateColorAsState(if (all) accent.copy(alpha = 0.16f) else Color.White.copy(alpha = 0.08f), label = "bg")
    Row(
        Modifier.height(52.dp).clip(RoundedCornerShape(50)).background(bg)
            .border(0.75.dp, Color.White.copy(alpha = 0.1f), RoundedCornerShape(50))
            .pressable(onClick, enabled = !all)
            .padding(start = 10.dp, end = 18.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(34.dp), contentAlignment = Alignment.Center) {
            Canvas(Modifier.size(34.dp)) {
                val sw = 2.5.dp.toPx()
                drawCircle(Color.White.copy(alpha = 0.1f), style = Stroke(sw))
                drawArc(accent, -90f, 360f * frac, false, style = Stroke(sw, cap = StrokeCap.Round),
                    topLeft = Offset(sw / 2, sw / 2), size = Size(size.width - sw, size.height - sw))
            }
            Icon(if (all) Icons.Rounded.DownloadDone else Icons.Rounded.CloudDownload, null,
                tint = if (all) accent else Ink.Text, modifier = Modifier.size(17.dp))
        }
        HGap(10.dp)
        AnimatedContent(
            when {
                all -> "All saved"
                running > 0 -> "Saving ${(frac * 100).toInt()}%"
                have > 0 -> "Save the rest · ${formatBytes(catalog.totalBytes - have)}"
                else -> "Save all · ${formatBytes(catalog.totalBytes)}"
            },
            transitionSpec = { fadeIn(tween(180)) togetherWith fadeOut(tween(120)) },
            label = "dlall",
        ) { Text(it, style = Type.Label, color = Ink.Text) }
    }
}

@Composable
private fun Segmented(options: List<String>, selected: Int, onSelect: (Int) -> Unit, modifier: Modifier = Modifier) {
    BoxWithConstraints(
        modifier.fillMaxWidth().height(40.dp).clip(RoundedCornerShape(50)).background(Color.White.copy(alpha = 0.05f))
            .border(0.75.dp, Color.White.copy(alpha = 0.07f), RoundedCornerShape(50)).padding(4.dp)
    ) {
        val w = maxWidth / options.size
        val x by animateDpAsState(w * selected, spring(0.72f, 520f), label = "seg")
        Box(Modifier.offset(x = x).width(w).fillMaxSize().clip(RoundedCornerShape(50)).background(Color.White.copy(alpha = 0.12f)))
        Row(Modifier.fillMaxSize()) {
            options.forEachIndexed { i, label ->
                val c by animateColorAsState(if (i == selected) Ink.Text else Ink.Muted, label = "segc")
                Box(Modifier.weight(1f).fillMaxSize().pressable({ onSelect(i) }, pressedScale = 0.97f), contentAlignment = Alignment.Center) {
                    Text(label, style = Type.Label, color = c)
                }
            }
        }
    }
}

@Composable
fun TrackRow(
    track: Track,
    state: DownloadState,
    current: Boolean,
    playing: Boolean,
    available: Boolean,
    onPlay: () -> Unit,
    onDownload: () -> Unit,
    onRemove: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val cover by rememberCover(track.thumb ?: track.cover)
    val accent = cover?.colors?.accent ?: LocalCoverColors.current.accent
    val bg by animateColorAsState(if (current) accent.copy(alpha = 0.13f) else Color.Transparent, tween(380), label = "rowbg")
    val titleColor by animateColorAsState(if (current) accent else Ink.Text, tween(380), label = "rowt")
    val dim by animateFloatAsState(if (available) 1f else 0.42f, label = "dim")
    Row(
        modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 2.dp)
            .clip(RoundedCornerShape(20.dp))
            .background(bg)
            .pressable(onPlay, pressedScale = 0.975f)
            .padding(horizontal = 12.dp, vertical = 10.dp)
            .graphicsLayer { alpha = dim },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(contentAlignment = Alignment.Center) {
            Cover(track.thumb ?: track.cover, 56.dp, 14.dp)
            androidx.compose.animation.AnimatedVisibility(current, enter = fadeIn(tween(220)), exit = fadeOut(tween(220))) {
                Box(Modifier.size(56.dp).clip(RoundedCornerShape(14.dp)).background(Color.Black.copy(alpha = 0.45f)), contentAlignment = Alignment.Center) {
                    Equalizer(playing, Color.White, Modifier.size(22.dp, 18.dp))
                }
            }
        }
        HGap(14.dp)
        Column(Modifier.weight(1f)) {
            Text(track.title, style = Type.Row, color = titleColor, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Gap(3.dp)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(formatDuration(track.durationMs), style = Type.Mono, color = Ink.Muted)
                Dot()
                Text("${track.bpm} BPM", style = Type.Meta, color = Ink.Muted)
                Dot()
                Text(track.key, style = Type.Meta, color = Ink.Muted, maxLines = 1)
            }
            AnimatedVisibility(state is DownloadState.Failed, enter = expandVertically() + fadeIn(), exit = shrinkVertically() + fadeOut()) {
                Text("Couldn't save · tap to retry", style = Type.Meta, color = Ink.Error, modifier = Modifier.padding(top = 3.dp))
            }
        }
        HGap(8.dp)
        DownloadButton(state, accent, onDownload, onLongClick = if (state is DownloadState.Saved) onRemove else null)
    }
}

@Composable
private fun Dot() = Box(Modifier.padding(horizontal = 7.dp).size(3.dp).clip(CircleShape).background(Ink.Faint))

