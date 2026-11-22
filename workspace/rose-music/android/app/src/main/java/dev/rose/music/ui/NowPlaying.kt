package dev.rose.music.ui

import android.os.Build
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.basicMarquee
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.PagerDefaults
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.KeyboardArrowDown
import androidx.compose.material.icons.rounded.Repeat
import androidx.compose.material.icons.rounded.RepeatOne
import androidx.compose.material.icons.rounded.Shuffle
import androidx.compose.material.icons.rounded.SkipNext
import androidx.compose.material.icons.rounded.SkipPrevious
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.blur
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.util.lerp
import androidx.media3.common.Player
import dev.rose.music.Catalog
import dev.rose.music.DownloadState
import dev.rose.music.PlayerUi
import dev.rose.music.Track
import dev.rose.music.formatDuration
import dev.rose.music.rememberCover
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.launch
import kotlin.math.abs
import kotlin.math.absoluteValue

class PlayerActions(
    val toggle: () -> Unit,
    val next: () -> Unit,
    val previous: () -> Unit,
    val seekTo: (Long) -> Unit,
    val skipTo: (Track) -> Unit,
    val shuffle: (Boolean) -> Unit,
    val cycleRepeat: () -> Unit,
    val download: (Track) -> Unit,
    val remove: (Track) -> Unit,
)

/** The floating mini player: tap or swipe up to open, swipe sideways to change song. */
@Composable
fun MiniPlayer(track: Track, ui: PlayerUi, actions: PlayerActions, onOpen: () -> Unit, modifier: Modifier = Modifier) {
    val colors = LocalCoverColors.current
    val haptics = rememberHaptics()
    var dx by remember { mutableFloatStateOf(0f) }
    val settle = remember { Animatable(0f) }
    val scope = rememberCoroutineScope()
    val frac = if (ui.durationMs > 0) ui.positionMs.toFloat() / ui.durationMs else 0f
    Box(
        modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp)
            .shadow(28.dp, RoundedCornerShape(26.dp), ambientColor = colors.glow, spotColor = colors.glow)
            .clip(RoundedCornerShape(26.dp))
            .background(Brush.horizontalGradient(listOf(lerp(colors.deep, Ink.Raised, 0.35f), Ink.Raised)))
            .border(0.75.dp, Color.White.copy(alpha = 0.09f), RoundedCornerShape(26.dp))
            .pointerInput(Unit) {
                detectVerticalDragGestures { change, amount -> if (amount < -12) { change.consume(); onOpen() } }
            }
            .pointerInput(track.id) {
                detectHorizontalDragGestures(
                    onDragEnd = {
                        val go = dx
                        scope.launch {
                            if (abs(go) > 120) { haptics.confirm(); if (go < 0) actions.next() else actions.previous() }
                            settle.snapTo(go); dx = 0f; settle.animateTo(0f, spring(0.6f, 500f))
                        }
                    },
                ) { change, amount -> change.consume(); dx += amount }
            }
            .pressable(onOpen, pressedScale = 0.985f)
            .semantics { contentDescription = "Now playing ${track.title}. Open player" }
    ) {
        Row(
            Modifier.graphicsLayer { translationX = (dx + settle.value) * 0.6f }.padding(10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            val spin by animateFloatAsState(if (ui.playing) 1f else 0.9f, spring(0.5f, 300f), label = "mini")
            Box(Modifier.graphicsLayer { scaleX = spin; scaleY = spin }) { Cover(track.thumb ?: track.cover, 50.dp, 14.dp) }
            HGap(12.dp)
            Column(Modifier.weight(1f)) {
                AnimatedContent(
                    track,
                    transitionSpec = { (slideInHorizontally { it / 3 } + fadeIn()) togetherWith (slideOutHorizontally { -it / 3 } + fadeOut()) },
                    label = "minititle",
                ) { t ->
                    Column {
                        Text(t.title, style = Type.Row, color = Ink.Text, maxLines = 1, modifier = Modifier.basicMarquee())
                        Text(t.artist, style = Type.Meta, color = Ink.Muted, maxLines = 1)
                    }
                }
            }
            Box(
                Modifier.size(48.dp).clip(CircleShape).background(Color.White.copy(alpha = 0.1f))
                    .pressable({ haptics.tap(); actions.toggle() }, pressedScale = 0.85f),
                contentAlignment = Alignment.Center,
            ) { PlayPauseIcon(ui.playing, Ink.Text, Modifier.size(28.dp)) }
            Box(
                Modifier.size(44.dp).semantics { contentDescription = "Next" }
                    .pressable({ haptics.tap(); actions.next() }, pressedScale = 0.85f),
                contentAlignment = Alignment.Center,
            ) { Icon(Icons.Rounded.SkipNext, null, tint = Ink.Text, modifier = Modifier.size(28.dp)) }
        }
        Canvas(Modifier.align(Alignment.BottomStart).fillMaxWidth().height(2.5.dp).padding(horizontal = 22.dp)) {
            drawRoundRect(Color.White.copy(alpha = 0.1f), cornerRadius = androidx.compose.ui.geometry.CornerRadius(4f))
            drawRoundRect(colors.accent, size = size.copy(width = size.width * frac.coerceIn(0f, 1f)), cornerRadius = androidx.compose.ui.geometry.CornerRadius(4f))
        }
    }
}

/**
 * The full player. In a sheet ([onClose] set) it drags down to dismiss; on an open Fold it lives as a side pane.
 */
@Composable
fun PlayerPane(
    catalog: Catalog,
    queue: List<Track>,
    track: Track,
    ui: PlayerUi,
    download: DownloadState,
    actions: PlayerActions,
    onClose: (() -> Unit)?,
    modifier: Modifier = Modifier,
) {
    val colors = LocalCoverColors.current
    val haptics = rememberHaptics()
    val scope = rememberCoroutineScope()
    val drag = remember { Animatable(0f) }

    Box(
        modifier
            .fillMaxSize()
            .graphicsLayer {
                translationY = drag.value
                val p = (drag.value / size.height).coerceIn(0f, 1f)
                scaleX = 1f - p * 0.08f; scaleY = scaleX
                shape = RoundedCornerShape((p * 120).dp); clip = onClose != null
            }
            .then(
                if (onClose == null) Modifier else Modifier.pointerInput(Unit) {
                    detectVerticalDragGestures(
                        onDragEnd = {
                            scope.launch {
                                if (drag.value > size.height * 0.18f) { haptics.tap(); onClose() } else drag.animateTo(0f, spring(0.7f, 400f))
                            }
                        },
                    ) { change, amount -> change.consume(); scope.launch { drag.snapTo((drag.value + amount).coerceAtLeast(0f)) } }
                }
            )
    ) {
        Backdrop(track)
        Column(
            Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().padding(horizontal = 24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            // Top bar
            Row(Modifier.fillMaxWidth().height(56.dp), verticalAlignment = Alignment.CenterVertically) {
                if (onClose != null) {
                    Box(Modifier.size(44.dp).semantics { contentDescription = "Close player" }.pressable({ haptics.tap(); onClose() }), contentAlignment = Alignment.Center) {
                        Icon(Icons.Rounded.KeyboardArrowDown, null, tint = Ink.Text, modifier = Modifier.size(32.dp))
                    }
                } else Spacer(Modifier.size(44.dp))
                Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("PLAYING FROM", style = Type.Eyebrow, color = Ink.Text.copy(alpha = 0.6f))
                    Text(catalog.album, style = Type.Label, color = Ink.Text, maxLines = 1)
                }
                DownloadButton(download, colors.accent, onClick = { haptics.tap(); actions.download(track) }, size = 44.dp)
            }
            BoxWithConstraints(Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
                val side = minOf(maxWidth, maxHeight - 8.dp)
                CoverPager(queue, track, ui.playing, side, onSettle = { t -> haptics.tick(); actions.skipTo(t) })
            }
            Gap(20.dp)
            // Title block
            AnimatedContent(
                track,
                transitionSpec = { (fadeIn(tween(260)) + slideInHorizontally { it / 6 }) togetherWith fadeOut(tween(160)) },
                label = "title", modifier = Modifier.fillMaxWidth(),
            ) { t ->
                Column(Modifier.fillMaxWidth()) {
                    Text(t.title, style = Type.Title, color = Ink.Text, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.basicMarquee())
                    Gap(4.dp)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(t.artist, style = Type.Label, color = Ink.Text.copy(alpha = 0.7f))
                        HGap(10.dp)
                        Chip("${t.bpm} BPM"); HGap(6.dp); Chip(t.key)
                        if (download is DownloadState.Saved) { HGap(6.dp); Chip("Saved", colors.accent) }
                    }
                }
            }
            Gap(14.dp)
            WaveformScrubber(track.peaks, ui.positionMs, ui.durationMs.takeIf { it > 0 } ?: track.durationMs, colors.accent, actions.seekTo)
            Row(Modifier.fillMaxWidth()) {
                Text(formatDuration(ui.positionMs), style = Type.Mono, color = Ink.Text.copy(alpha = 0.7f))
                Spacer(Modifier.weight(1f))
                Text("-" + formatDuration(((ui.durationMs.takeIf { it > 0 } ?: track.durationMs) - ui.positionMs).coerceAtLeast(0)), style = Type.Mono, color = Ink.Text.copy(alpha = 0.7f))
            }
            Gap(14.dp)
            Transport(ui, actions)
            Gap(24.dp)
        }
    }
}

@Composable
private fun Backdrop(track: Track) {
    val colors = LocalCoverColors.current
    val cover by rememberCover(track.cover)
    Box(Modifier.fillMaxSize().background(Ink.Night)) {
        Aurora(colors.accent, colors.deep, colors.glow, intensity = 1.2f)
        AnimatedContent(cover, transitionSpec = { fadeIn(tween(700)) togetherWith fadeOut(tween(700)) }, label = "bd") { c ->
            if (c != null && Build.VERSION.SDK_INT >= 31) {
                Image(
                    c.image, null, contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize().graphicsLayer { alpha = 0.55f; scaleX = 1.3f; scaleY = 1.3f }.blur(90.dp),
                )
            }
        }
        Box(Modifier.fillMaxSize().background(Brush.verticalGradient(0f to Ink.Night.copy(alpha = 0.25f), 0.5f to Ink.Night.copy(alpha = 0.35f), 1f to Ink.Night.copy(alpha = 0.9f))))
    }
}

/** Covers side by side: swipe to change song. The cover breathes in when playing and sinks back when paused. */
@Composable
private fun CoverPager(queue: List<Track>, current: Track, playing: Boolean, side: androidx.compose.ui.unit.Dp, onSettle: (Track) -> Unit) {
    val start = queue.indexOf(current).coerceAtLeast(0)
    val pager = rememberPagerState(initialPage = start) { queue.size }
    LaunchedEffect(current.id) {
        val i = queue.indexOf(current)
        if (i >= 0 && i != pager.currentPage && !pager.isScrollInProgress) pager.animateScrollToPage(i, animationSpec = spring(0.85f, 220f))
    }
    LaunchedEffect(pager) {
        snapshotFlow { pager.settledPage }.distinctUntilChanged().drop(1).collect { p ->
            queue.getOrNull(p)?.let { if (it.id != current.id) onSettle(it) }
        }
    }
    val breathe by animateFloatAsState(if (playing) 1f else 0.84f, spring(0.55f, 160f), label = "breathe")
    HorizontalPager(
        state = pager,
        modifier = Modifier.fillMaxWidth().height(side),
        flingBehavior = PagerDefaults.flingBehavior(pager),
        beyondViewportPageCount = 1,
        key = { queue[it].id },
    ) { page ->
        val t = queue[page]
        val offset = ((pager.currentPage - page) + pager.currentPageOffsetFraction).absoluteValue.coerceIn(0f, 1f)
        val cover by rememberCover(t.cover)
        val glow = cover?.colors?.glow ?: Color.Black
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Box(
                Modifier
                    .size(side)
                    .graphicsLayer {
                        val s = lerp(breathe, 0.78f, offset)
                        scaleX = s; scaleY = s
                        alpha = lerp(1f, 0.4f, offset)
                        rotationY = (pager.currentPage - page + pager.currentPageOffsetFraction) * -18f
                        cameraDistance = 14f * density
                    }
                    .shadow(lerp(48f, 8f, offset).dp, RoundedCornerShape(28.dp), ambientColor = glow, spotColor = glow)
            ) { Cover(t.cover, side, 28.dp) }
        }
    }
}

@Composable
private fun Chip(text: String, tint: Color = Ink.Text) {
    Box(
        Modifier.clip(RoundedCornerShape(50)).background(tint.copy(alpha = 0.12f)).padding(horizontal = 8.dp, vertical = 3.dp)
    ) { Text(text, style = Type.Meta.copy(fontSize = Type.Meta.fontSize * 0.88f), color = tint.copy(alpha = 0.92f), maxLines = 1) }
}

@Composable
private fun Transport(ui: PlayerUi, actions: PlayerActions) {
    val colors = LocalCoverColors.current
    val haptics = rememberHaptics()
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.SpaceBetween) {
        Toggle(Icons.Rounded.Shuffle, "Shuffle", ui.shuffle, colors.accent) { haptics.tick(); actions.shuffle(!ui.shuffle) }
        Round(Icons.Rounded.SkipPrevious, "Previous", 60) { haptics.tap(); actions.previous() }
        val pressScale by animateFloatAsState(if (ui.playing) 1f else 1.06f, spring(0.4f, 400f), label = "big")
        Box(
            Modifier.size(84.dp).graphicsLayer { scaleX = pressScale; scaleY = pressScale }
                .shadow(30.dp, CircleShape, ambientColor = colors.glow, spotColor = colors.glow)
                .clip(CircleShape).background(Ink.Text)
                .pressable({ haptics.confirm(); actions.toggle() }, pressedScale = 0.88f),
            contentAlignment = Alignment.Center,
        ) {
            if (ui.buffering) Spinner(colors.deep, Modifier.size(78.dp))
            PlayPauseIcon(ui.playing, Ink.Night, Modifier.size(44.dp))
        }
        Round(Icons.Rounded.SkipNext, "Next", 60) { haptics.tap(); actions.next() }
        Toggle(
            if (ui.repeat == Player.REPEAT_MODE_ONE) Icons.Rounded.RepeatOne else Icons.Rounded.Repeat,
            when (ui.repeat) { Player.REPEAT_MODE_ALL -> "Repeat all"; Player.REPEAT_MODE_ONE -> "Repeat one"; else -> "Repeat off" },
            ui.repeat != Player.REPEAT_MODE_OFF, colors.accent,
        ) { haptics.tick(); actions.cycleRepeat() }
    }
}

@Composable
private fun Round(icon: androidx.compose.ui.graphics.vector.ImageVector, label: String, size: Int, onClick: () -> Unit) {
    Box(Modifier.size(size.dp).semantics { contentDescription = label }.clip(CircleShape).pressable(onClick, pressedScale = 0.82f), contentAlignment = Alignment.Center) {
        Icon(icon, null, tint = Ink.Text, modifier = Modifier.size((size * 0.6f).dp))
    }
}

@Composable
private fun Toggle(icon: androidx.compose.ui.graphics.vector.ImageVector, label: String, on: Boolean, accent: Color, onClick: () -> Unit) {
    val tint by androidx.compose.animation.animateColorAsState(if (on) accent else Ink.Text.copy(alpha = 0.6f), label = "tg")
    val dot by animateFloatAsState(if (on) 1f else 0f, spring(0.5f, 500f), label = "dot")
    Column(
        Modifier.size(48.dp).semantics { contentDescription = label + if (on) ", on" else ", off" }.pressable(onClick, pressedScale = 0.82f),
        horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center,
    ) {
        Icon(icon, null, tint = tint, modifier = Modifier.size(24.dp))
        Gap(4.dp)
        Box(Modifier.size(4.dp).graphicsLayer { scaleX = dot; scaleY = dot }.clip(CircleShape).background(accent))
    }
}

private fun lerp(a: Color, b: Color, f: Float) = androidx.compose.ui.graphics.lerp(a, b, f)
