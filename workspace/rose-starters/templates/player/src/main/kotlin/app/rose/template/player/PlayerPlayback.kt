package app.rose.template.player

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.media.MediaPlayer
import android.os.Build
import android.os.Handler
import android.os.Looper
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.runtime.setValue

internal const val MINIMUM_PASSAGE_MILLIS = 1_000

internal class PlayerState(position: Int = 0, start: Int = 8_000, end: Int = 16_000, loop: Boolean = false) {
    var position by mutableIntStateOf(position)
    var start by mutableIntStateOf(start)
    var end by mutableIntStateOf(end)
    var loop by mutableStateOf(loop)

    val passage get() = start until end

    companion object {
        val Saver = listSaver(
            save = { listOf(it.position, it.start, it.end, it.loop) },
            restore = { PlayerState(it[0] as Int, it[1] as Int, it[2] as Int, it[3] as Boolean) },
        )
    }
}

/** Owns one foreground audio session. All transport calls and engine callbacks run on the main thread. */
internal class PlayerPlayback(context: Context, private val state: PlayerState) {
    private val appContext = context.applicationContext
    private val handler = Handler(Looper.getMainLooper())
    private val audio = appContext.getSystemService(AudioManager::class.java)
    private val attributes = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_MEDIA)
        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
        .build()
    private val focusListener = AudioManager.OnAudioFocusChangeListener { change ->
        if (change < 0) pause()
    }
    private val focusRequest = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
        AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
            .setAudioAttributes(attributes)
            .setWillPauseWhenDucked(true)
            .setOnAudioFocusChangeListener(focusListener, handler)
            .build()
    } else null
    private var hasFocus = false
    private var media: MediaPlayer? = null
    private var seeking = false
    private var queuedSeek: Int? = null
    private val readPosition = Runnable { updatePosition() }

    var duration by mutableIntStateOf(0)
        private set
    var playing by mutableStateOf(false)
        private set
    var message by mutableStateOf<String?>(null)
        private set
    val ready get() = duration > 0

    init {
        openMedia()?.let { player ->
            duration = player.duration
            if (duration < 2 * MINIMUM_PASSAGE_MILLIS) {
                unavailable()
            } else {
                state.start = state.start.coerceIn(0, duration - MINIMUM_PASSAGE_MILLIS)
                state.end = state.end.coerceIn(state.start + MINIMUM_PASSAGE_MILLIS, duration)
                if (state.position > 0) seek(state.position.coerceAtMost(duration))
            }
        }
    }

    fun play() {
        if (playing || !ready || openMedia() == null) return
        if (!requestFocus()) {
            message = "Audio is in use. Try Play again."
            return
        }
        message = null
        playing = true
        when {
            state.loop && state.position !in state.passage -> seek(state.start)
            state.position >= duration -> seek(0)
            else -> startWhenReady()
        }
    }

    fun pause() {
        if (!playing) return
        playing = false
        handler.removeCallbacks(readPosition)
        media?.let { player ->
            if (player.isPlaying) player.pause()
            if (!seeking) state.position = player.currentPosition.coerceIn(0, duration)
        }
        abandonFocus()
    }

    fun scrub(position: Int) {
        if (!ready) return
        val target = position.coerceIn(0, duration)
        if (state.loop && target !in state.passage) state.loop = false
        seek(target)
    }

    fun setLoop(enabled: Boolean) {
        state.loop = enabled
        if (enabled && state.position !in state.passage) seek(state.start)
    }

    fun setStart() {
        if (state.position <= state.end - MINIMUM_PASSAGE_MILLIS) state.start = state.position
    }

    fun setEnd() {
        if (state.position >= state.start + MINIMUM_PASSAGE_MILLIS) state.end = state.position
    }

    fun close() {
        pause()
        releaseMedia()
    }

    private fun openMedia(): MediaPlayer? {
        media?.let { return it }
        val player = MediaPlayer.create(
            appContext, R.raw.between_stations, attributes, AudioManager.AUDIO_SESSION_ID_GENERATE,
        ) ?: run {
            unavailable()
            return null
        }
        media = player
        player.setOnSeekCompleteListener {
            if (it === media) onSeekComplete()
        }
        player.setOnCompletionListener {
            if (it === media) onCompletion()
        }
        player.setOnErrorListener { source, _, _ ->
            if (source === media) unavailable()
            true
        }
        return player
    }

    private fun seek(target: Int) {
        if (seeking) {
            state.position = target
            queuedSeek = target
            return
        }
        val player = openMedia() ?: return
        state.position = target
        seeking = true
        handler.removeCallbacks(readPosition)
        if (player.isPlaying) player.pause()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            player.seekTo(target.toLong(), MediaPlayer.SEEK_CLOSEST)
        } else {
            @Suppress("DEPRECATION")
            player.seekTo(target)
        }
    }

    private fun onSeekComplete() {
        seeking = false
        val next = queuedSeek
        queuedSeek = null
        if (next == null) startWhenReady() else seek(next)
    }

    private fun startWhenReady() {
        if (!playing || seeking) return
        media?.start()
        handler.removeCallbacks(readPosition)
        handler.postDelayed(readPosition, POSITION_INTERVAL_MILLIS)
    }

    private fun updatePosition() {
        val player = media ?: return
        if (!playing || seeking) return
        val position = player.currentPosition.coerceIn(0, duration)
        if (state.loop && position >= state.end) {
            seek(state.start)
        } else {
            state.position = position
            handler.postDelayed(readPosition, POSITION_INTERVAL_MILLIS)
        }
    }

    private fun onCompletion() {
        if (seeking) return
        // A fresh prepared engine can start at any seek position after end-of-file.
        releaseMedia()
        if (playing && state.loop) {
            seek(state.start)
        } else {
            state.position = duration
            playing = false
            abandonFocus()
        }
    }

    private fun unavailable() {
        playing = false
        duration = 0
        message = "Audio could not be loaded."
        abandonFocus()
        releaseMedia()
    }

    private fun releaseMedia() {
        handler.removeCallbacks(readPosition)
        seeking = false
        queuedSeek = null
        val player = media
        media = null
        player?.release()
    }

    @Suppress("DEPRECATION")
    private fun requestFocus(): Boolean {
        val result = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && focusRequest != null) {
            audio.requestAudioFocus(focusRequest)
        } else {
            audio.requestAudioFocus(focusListener, AudioManager.STREAM_MUSIC, AudioManager.AUDIOFOCUS_GAIN)
        }
        hasFocus = result == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
        return hasFocus
    }

    @Suppress("DEPRECATION")
    private fun abandonFocus() {
        if (!hasFocus) return
        hasFocus = false
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && focusRequest != null) {
            audio.abandonAudioFocusRequest(focusRequest)
        } else {
            audio.abandonAudioFocus(focusListener)
        }
    }

    private companion object {
        const val POSITION_INTERVAL_MILLIS = 40L
    }
}
