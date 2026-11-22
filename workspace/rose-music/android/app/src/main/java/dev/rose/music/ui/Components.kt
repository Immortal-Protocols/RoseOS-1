package dev.rose.music.ui

import android.graphics.Bitmap
import android.os.Build
import android.view.HapticFeedbackConstants
import android.view.View
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.indication
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.ArrowDownward
import androidx.compose.material.icons.rounded.Check
import androidx.compose.material.icons.rounded.Pause
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.ImageShader
import androidx.compose.ui.graphics.ShaderBrush
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.TileMode
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import dev.rose.music.DownloadState
import dev.rose.music.LibraryStatus
import dev.rose.music.formatDuration
import dev.rose.music.rememberCover
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.random.Random

// ---------- feel ----------

/** Haptics that read as intentional: a tick for small steps, a confirm for completions. */
class Haptics(private val view: View) {
    fun tick() = view.performHapticFeedback(if (Build.VERSION.SDK_INT >= 34) HapticFeedbackConstants.SEGMENT_FREQUENT_TICK else HapticFeedbackConstants.CLOCK_TICK)
    fun tap() = view.performHapticFeedback(HapticFeedbackConstants.CONTEXT_CLICK)
    fun confirm() = view.performHapticFeedback(if (Build.VERSION.SDK_INT >= 30) HapticFeedbackConstants.CONFIRM else HapticFeedbackConstants.LONG_PRESS)
    fun reject() = view.performHapticFeedback(if (Build.VERSION.SDK_INT >= 30) HapticFeedbackConstants.REJECT else HapticFeedbackConstants.LONG_PRESS)
    fun heavy() = view.performHapticFeedback(if (Build.VERSION.SDK_INT >= 34) HapticFeedbackConstants.DRAG_START else HapticFeedbackConstants.LONG_PRESS)
}

@Composable
fun rememberHaptics(): Haptics { val v = LocalView.current; return remember(v) { Haptics(v) } }

/** Springs down a little while pressed and settles back with a touch of overshoot. */
fun Modifier.pressable(
    onClick: () -> Unit,
    role: Role = Role.Button,
    pressedScale: Float = 0.96f,
    enabled: Boolean = true,
    onLongClick: (() -> Unit)? = null,
): Modifier = composed {
    val source = remember { MutableInteractionSource() }
    val pressed by source.collectIsPressedAsState()
    val scale by animateFloatAsState(
        if (pressed) pressedScale else 1f,
        spring(dampingRatio = if (pressed) 1f else 0.45f, stiffness = if (pressed) 1400f else 500f),
        label = "press",
    )
    this
        .graphicsLayer { scaleX = scale; scaleY = scale }
        .combinedClickable(
            interactionSource = source, indication = null, enabled = enabled, role = role,
            onLongClick = onLongClick, onClick = onClick,
        )
}

// ---------- surfaces ----------

/** A slow aurora in the cover's colours, with film grain, behind everything. */
@Composable
fun Aurora(accent: Color, deep: Color, glow: Color, modifier: Modifier = Modifier, intensity: Float = 1f) {
    val t = rememberInfiniteTransition(label = "aurora")
    val a by t.animateFloat(0f, (2 * PI).toFloat(), infiniteRepeatable(tween(22000, easing = LinearEasing)), label = "a")
    val grain = remember { grainTile() }
    Canvas(modifier.fillMaxSize()) {
        drawRect(lerp(Ink.Night, deep, 0.55f))
        fun blob(cx: Float, cy: Float, r: Float, c: Color, alpha: Float) {
            drawCircle(
                Brush.radialGradient(listOf(c.copy(alpha = alpha * intensity), Color.Transparent), Offset(cx, cy), r),
                r, Offset(cx, cy),
            )
        }
        val w = size.width; val h = size.height; val m = maxOf(w, h)
        blob(w * (0.2f + 0.12f * cos(a)), h * (0.12f + 0.06f * sin(a * 2)), m * 0.7f, glow, 0.55f)
        blob(w * (0.85f + 0.1f * sin(a)), h * (0.35f + 0.1f * cos(a)), m * 0.55f, accent, 0.28f)
        blob(w * (0.5f + 0.25f * sin(a + 1.3f)), h * (0.95f + 0.05f * cos(a * 2)), m * 0.6f, deep.copy(red = deep.red * 1.6f), 0.7f)
        drawRect(Brush.verticalGradient(0f to Color.Transparent, 0.55f to Ink.Night.copy(alpha = 0.55f), 1f to Ink.Night.copy(alpha = 0.92f)))
        drawRect(ShaderBrush(ImageShader(grain, TileMode.Repeated, TileMode.Repeated)), alpha = 0.07f)
    }
}

private fun grainTile(): ImageBitmap {
    val n = 128
    val r = Random(7)
    val px = IntArray(n * n) { val v = r.nextInt(256); (0xFF shl 24) or (v shl 16) or (v shl 8) or v }
    return Bitmap.createBitmap(px, n, n, Bitmap.Config.ARGB_8888).asImageBitmap()
}

@Composable
fun Cover(url: String?, size: Dp, corner: Dp, modifier: Modifier = Modifier, shape: Shape = RoundedCornerShape(corner)) {
    val cover by rememberCover(url)
    val shown by animateFloatAsState(if (cover != null) 1f else 0f, tween(420), label = "cover")
    Box(
        modifier.size(size).clip(shape)
            .background(Brush.linearGradient(listOf(Ink.Raised, Ink.Surface)))
    ) {
        cover?.let {
            Image(it.image, null, Modifier.fillMaxSize().graphicsLayer { alpha = shown; scaleX = 1.04f - 0.04f * shown; scaleY = scaleX }, contentScale = ContentScale.Crop)
        }
        Box(Modifier.fillMaxSize().border(0.75.dp, Color.White.copy(alpha = 0.08f), shape))
    }
}

// ---------- controls ----------

/** Download states morph in place: arrow → spinner → progress ring → a check that pops. */
@Composable
fun DownloadButton(state: DownloadState, accent: Color, onClick: () -> Unit, modifier: Modifier = Modifier, size: Dp = 40.dp, onLongClick: (() -> Unit)? = null) {
    val haptics = rememberHaptics()
    var lastSaved by remember { mutableStateOf(state is DownloadState.Saved) }
    LaunchedEffect(state) {
        val saved = state is DownloadState.Saved
        if (saved && !lastSaved) haptics.confirm()
        if (state is DownloadState.Failed) haptics.reject()
        lastSaved = saved
    }
    val desc = when (state) {
        DownloadState.None -> "Download"
        DownloadState.Queued -> "Waiting to download. Tap to cancel"
        is DownloadState.Running -> "Downloading ${(state.progress * 100).roundToInt()} percent. Tap to cancel"
        DownloadState.Saved -> "Saved on this phone"
        is DownloadState.Failed -> "Download failed. Tap to retry"
    }
    Box(
        modifier.size(size).semantics { contentDescription = desc }.pressable(onClick, pressedScale = 0.86f, onLongClick = onLongClick),
        contentAlignment = Alignment.Center,
    ) {
        AnimatedContent(
            targetState = when (state) { is DownloadState.Running -> "run"; else -> state::class.simpleName ?: "" },
            transitionSpec = {
                (scaleIn(spring(0.5f, 600f), initialScale = 0.4f) + fadeIn(tween(160))) togetherWith
                    (scaleOut(tween(140), targetScale = 0.6f) + fadeOut(tween(120)))
            },
            label = "dl",
        ) { key ->
            Box(Modifier.size(size), contentAlignment = Alignment.Center) {
                when (key) {
                    "None" -> {
                        Box(Modifier.size(size - 6.dp).border(1.5.dp, Ink.Muted.copy(alpha = 0.45f), CircleShape))
                        Icon(Icons.Rounded.ArrowDownward, null, tint = Ink.Text, modifier = Modifier.size(size * 0.46f))
                    }
                    "Queued" -> Spinner(accent, Modifier.size(size - 6.dp))
                    "run" -> {
                        val p = (state as? DownloadState.Running)?.progress ?: 0f
                        val shown by animateFloatAsState(p, spring(stiffness = 120f), label = "p")
                        Canvas(Modifier.size(size - 6.dp)) {
                            val sw = 2.5.dp.toPx()
                            drawCircle(Color.White.copy(alpha = 0.1f), style = Stroke(sw))
                            drawArc(accent, -90f, 360f * shown, false, style = Stroke(sw, cap = StrokeCap.Round),
                                topLeft = Offset(sw / 2, sw / 2), size = Size(this.size.width - sw, this.size.height - sw))
                            val s = this.size.width * 0.2f
                            drawRoundRect(Ink.Text, Offset(center.x - s / 2, center.y - s / 2), Size(s, s), CornerRadius(2.dp.toPx()))
                        }
                    }
                    "Saved" -> Box(Modifier.size(size - 10.dp).clip(CircleShape).background(accent), contentAlignment = Alignment.Center) {
                        Icon(Icons.Rounded.Check, null, tint = Ink.Night, modifier = Modifier.size(size * 0.44f))
                    }
                    else -> {
                        Box(Modifier.size(size - 6.dp).border(1.5.dp, Ink.Error.copy(alpha = 0.7f), CircleShape))
                        Icon(Icons.Rounded.Refresh, null, tint = Ink.Error, modifier = Modifier.size(size * 0.46f))
                    }
                }
            }
        }
    }
}

@Composable
fun Spinner(color: Color, modifier: Modifier = Modifier) {
    val t = rememberInfiniteTransition(label = "spin")
    val r by t.animateFloat(0f, 360f, infiniteRepeatable(tween(900, easing = LinearEasing)), label = "r")
    Canvas(modifier) {
        val sw = 2.5.dp.toPx()
        drawCircle(Color.White.copy(alpha = 0.1f), style = Stroke(sw))
        drawArc(color, r, 80f, false, style = Stroke(sw, cap = StrokeCap.Round), topLeft = Offset(sw / 2, sw / 2),
            size = Size(size.width - sw, size.height - sw))
    }
}

/** Four bars that dance while playing and settle low when paused. */
@Composable
fun Equalizer(playing: Boolean, color: Color, modifier: Modifier = Modifier) {
    val t = rememberInfiniteTransition(label = "eq")
    val phases = listOf(380, 520, 300, 450)
    val vals = phases.mapIndexed { i, d ->
        t.animateFloat(0.15f, 1f, infiniteRepeatable(tween(d, easing = FastOutSlowInEasing), RepeatMode.Reverse, initialStartOffset = androidx.compose.animation.core.StartOffset(i * 90)), label = "b$i")
    }
    val level by animateFloatAsState(if (playing) 1f else 0f, tween(400), label = "lvl")
    Canvas(modifier) {
        val n = 4; val gap = size.width * 0.12f; val bw = (size.width - gap * (n - 1)) / n
        for (i in 0 until n) {
            val h = size.height * (0.18f + 0.82f * vals[i].value * level).coerceAtMost(1f)
            drawRoundRect(color, Offset(i * (bw + gap), size.height - h), Size(bw, h), CornerRadius(bw / 2))
        }
    }
}

@Composable
fun PlayPauseIcon(playing: Boolean, tint: Color, modifier: Modifier = Modifier) {
    AnimatedContent(
        playing,
        transitionSpec = {
            (scaleIn(spring(0.55f, 700f), initialScale = 0.5f) + fadeIn(tween(120))) togetherWith
                (scaleOut(tween(120), targetScale = 0.5f) + fadeOut(tween(100)))
        },
        label = "pp",
    ) { p ->
        Icon(if (p) Icons.Rounded.Pause else Icons.Rounded.PlayArrow, if (p) "Pause" else "Play", tint = tint, modifier = modifier)
    }
}

/**
 * The song's real loudness shape as a scrubber. Drag or tap anywhere: bars near the finger swell, and each bar
 * crossed ticks under the thumb.
 */
@Composable
fun WaveformScrubber(
    peaks: FloatArray,
    positionMs: Long,
    durationMs: Long,
    accent: Color,
    onSeek: (Long) -> Unit,
    modifier: Modifier = Modifier,
    barCount: Int = 64,
) {
    val haptics = rememberHaptics()
    var dragging by remember { mutableStateOf(false) }
    var dragFrac by remember { mutableFloatStateOf(0f) }
    val bars = remember(peaks, barCount) {
        if (peaks.isEmpty()) FloatArray(barCount) { 0.35f + 0.25f * sin(it * 0.7f) }
        else FloatArray(barCount) { i ->
            val a = i * peaks.size / barCount; val b = ((i + 1) * peaks.size / barCount).coerceAtLeast(a + 1)
            var m = 0f; for (k in a until b.coerceAtMost(peaks.size)) m = maxOf(m, peaks[k]); m
        }
    }
    val frac = if (dragging) dragFrac else if (durationMs > 0) (positionMs.toFloat() / durationMs).coerceIn(0f, 1f) else 0f
    val smooth by animateFloatAsState(frac, if (dragging) spring(stiffness = Spring.StiffnessHigh) else tween(110, easing = LinearEasing), label = "f")
    val lift by animateFloatAsState(if (dragging) 1f else 0f, spring(0.6f, 400f), label = "lift")
    val seek by rememberUpdatedState(onSeek)
    val dur by rememberUpdatedState(durationMs)
    val intro = remember { Animatable(0f) }
    LaunchedEffect(peaks) { intro.snapTo(0f); intro.animateTo(1f, tween(700, easing = FastOutSlowInEasing)) }
    var lastBar by remember { mutableStateOf(-1) }

    Canvas(
        modifier
            .fillMaxWidth()
            .height(72.dp)
            .semantics { contentDescription = "Seek. ${formatDuration(positionMs)} of ${formatDuration(durationMs)}" }
            .pointerInput(Unit) {
                detectTapGestures { o ->
                    val f = (o.x / size.width).coerceIn(0f, 1f)
                    haptics.tap(); seek((f * dur).toLong())
                }
            }
            .pointerInput(Unit) {
                detectHorizontalDragGestures(
                    onDragStart = { o -> dragging = true; dragFrac = (o.x / size.width).coerceIn(0f, 1f); haptics.heavy() },
                    onDragEnd = { dragging = false; seek((dragFrac * dur).toLong()); haptics.confirm() },
                    onDragCancel = { dragging = false },
                ) { change, _ ->
                    change.consume()
                    dragFrac = (change.position.x / size.width).coerceIn(0f, 1f)
                    val bar = (dragFrac * barCount).toInt()
                    if (bar != lastBar) { lastBar = bar; haptics.tick() }
                }
            }
    ) {
        val gap = size.width / barCount * 0.38f
        val bw = size.width / barCount - gap
        val mid = size.height / 2
        for (i in 0 until barCount) {
            val x = i * (bw + gap) + gap / 2
            val center = (i + 0.5f) / barCount
            val near = (1f - abs(center - smooth) * 9f).coerceIn(0f, 1f) * lift
            val stagger = ((intro.value * 1.6f) - i.toFloat() / barCount).coerceIn(0f, 1f)
            val h = (size.height * (0.1f + 0.62f * bars[i]) * (1f + 0.35f * near) * stagger).coerceAtMost(size.height)
            val played = center <= smooth
            val c = if (played) accent else Color.White.copy(alpha = 0.22f + 0.25f * near)
            drawRoundRect(c, Offset(x, mid - h / 2), Size(bw, h), CornerRadius(bw / 2))
        }
        // The thumb: a soft glow line at the play head.
        val px = smooth * size.width
        drawRoundRect(Color.White.copy(alpha = 0.85f), Offset(px - 1.5.dp.toPx(), mid - size.height * 0.45f),
            Size(3.dp.toPx(), size.height * 0.9f), CornerRadius(2.dp.toPx()))
        drawCircle(accent.copy(alpha = 0.25f + 0.25f * lift), radius = 10.dp.toPx() + 8.dp.toPx() * lift, center = Offset(px, mid))
    }
}

@Composable
fun StatusPill(status: LibraryStatus, savedCount: Int, modifier: Modifier = Modifier) {
    val (color, text) = when (status) {
        LibraryStatus.Loading -> Ink.Muted to "Connecting to Chariot…"
        LibraryStatus.Live -> Ink.Live to "Live from Chariot"
        LibraryStatus.Waking -> Ink.Warn to "Library is waking up · $savedCount saved"
        is LibraryStatus.Offline -> Ink.Warn to "Offline · $savedCount saved on this phone"
    }
    val t = rememberInfiniteTransition(label = "pulse")
    val pulse by t.animateFloat(0f, 1f, infiniteRepeatable(tween(1600, easing = LinearEasing)), label = "p")
    Row(
        modifier.clip(RoundedCornerShape(50)).background(Color.White.copy(alpha = 0.06f))
            .border(0.75.dp, Color.White.copy(alpha = 0.08f), RoundedCornerShape(50))
            .padding(horizontal = 12.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Canvas(Modifier.size(8.dp)) {
            if (status == LibraryStatus.Live || status == LibraryStatus.Loading) {
                drawCircle(color.copy(alpha = (1f - pulse) * 0.5f), radius = size.minDimension / 2 * (1f + pulse * 1.4f))
            }
            drawCircle(color)
        }
        AnimatedContent(text, transitionSpec = { fadeIn(tween(220)) togetherWith fadeOut(tween(160)) }, label = "st") {
            Text(it, style = Type.Meta, color = Ink.Text.copy(alpha = 0.86f))
        }
    }
}

@Composable
fun Hairline(modifier: Modifier = Modifier) = Spacer(modifier.fillMaxWidth().height(0.75.dp).background(Ink.Hairline))

@Composable
fun Gap(h: Dp) = Spacer(Modifier.height(h))

@Composable
fun HGap(w: Dp) = Spacer(Modifier.width(w))
