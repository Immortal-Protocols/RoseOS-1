package dev.rose.music

import android.content.ComponentName
import android.content.Context
import android.net.Uri
import androidx.compose.runtime.Immutable
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.guava.await
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

@Immutable
data class PlayerUi(
    val trackId: String? = null,
    val playing: Boolean = false,
    val buffering: Boolean = false,
    val positionMs: Long = 0,
    val durationMs: Long = 0,
    val shuffle: Boolean = false,
    val repeat: Int = Player.REPEAT_MODE_OFF,
    val error: String? = null,
)

/** The UI's side of playback: a MediaController bound to [PlaybackService], mirrored into a StateFlow. */
class PlayerConnection(context: Context, private val library: Library) {
    private val app = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var controller: MediaController? = null
    private var ticker: Job? = null
    private val _state = MutableStateFlow(PlayerUi())
    val state: StateFlow<PlayerUi> = _state.asStateFlow()

    private val listener = object : Player.Listener {
        override fun onEvents(player: Player, events: Player.Events) = publish()
        override fun onPlayerError(error: PlaybackException) {
            _state.value = _state.value.copy(error = if (error.errorCode in NETWORK_ERRORS)
                "Can't reach the library. Saved songs still play." else "Couldn't play this song.")
        }
    }

    fun connect() {
        if (controller != null) return
        scope.launch {
            val token = SessionToken(app, ComponentName(app, PlaybackService::class.java))
            val c = MediaController.Builder(app, token).buildAsync().await()
            controller = c
            c.addListener(listener)
            publish()
        }
    }

    fun release() {
        controller?.removeListener(listener)
        controller?.release()
        controller = null
        ticker?.cancel()
    }

    fun clearError() { _state.value = _state.value.copy(error = null) }

    private fun publish() {
        val c = controller ?: return
        _state.value = PlayerUi(
            trackId = c.currentMediaItem?.mediaId,
            // The intent to play, not the instant state, so the button doesn't flicker while buffering.
            playing = c.playWhenReady && c.playbackState != Player.STATE_ENDED && c.playbackState != Player.STATE_IDLE,
            buffering = c.playbackState == Player.STATE_BUFFERING,
            positionMs = c.currentPosition.coerceAtLeast(0),
            durationMs = c.duration.takeIf { it > 0 } ?: library.catalog.value?.byId(c.currentMediaItem?.mediaId)?.durationMs ?: 0,
            shuffle = c.shuffleModeEnabled,
            repeat = c.repeatMode,
            error = _state.value.error,
        )
        if (c.playWhenReady && ticker?.isActive != true) {
            ticker = scope.launch {
                while (isActive && controller?.playWhenReady == true) {
                    controller?.let { _state.value = _state.value.copy(positionMs = it.currentPosition.coerceAtLeast(0)) }
                    delay(100)
                }
            }
        }
    }

    private fun item(t: Track): MediaItem = MediaItem.Builder()
        .setMediaId(t.id)
        .setUri(library.playUri(t))
        .setMediaMetadata(
            MediaMetadata.Builder()
                .setTitle(t.title)
                .setArtist(t.artist)
                .setAlbumTitle(library.catalog.value?.album)
                .setTrackNumber(t.number)
                .setArtworkUri(t.cover?.let { Uri.parse(library.url(it)) })
                .setIsPlayable(true)
                .setIsBrowsable(false)
                .build()
        )
        .build()

    /** Plays [track] within [queue]. Reuses the current queue when it's the same list, so nothing re-buffers. */
    fun play(queue: List<Track>, track: Track) {
        val c = controller ?: return
        clearError()
        val ids = (0 until c.mediaItemCount).map { c.getMediaItemAt(it).mediaId }
        val index = queue.indexOf(track).coerceAtLeast(0)
        val stale = (0 until c.mediaItemCount).any { i ->
            val t = queue.getOrNull(i)
            t != null && c.getMediaItemAt(i).localConfiguration?.uri != library.playUri(t)
        }
        if (ids == queue.map { it.id } && !stale) {
            if (c.currentMediaItemIndex != index) c.seekTo(index, 0)
        } else {
            val keep = c.currentMediaItem?.mediaId == track.id
            val pos = if (keep) c.currentPosition else 0L
            c.setMediaItems(queue.map(::item), index, pos)
        }
        c.prepare()
        c.play()
    }

    fun toggle() {
        val c = controller ?: return
        when {
            c.playbackState == Player.STATE_ENDED -> { c.seekTo(c.currentMediaItemIndex, 0); c.play() }
            c.playWhenReady && c.playbackState != Player.STATE_IDLE -> c.pause()
            else -> { if (c.playbackState == Player.STATE_IDLE) c.prepare(); c.play() }
        }
    }
    fun next() { controller?.let { if (it.hasNextMediaItem()) it.seekToNextMediaItem() else it.seekTo(0, 0) } }
    fun previous() { controller?.seekToPrevious() }
    fun seekTo(ms: Long) { controller?.seekTo(ms); _state.value = _state.value.copy(positionMs = ms) }
    fun skipTo(trackId: String) {
        val c = controller ?: return
        (0 until c.mediaItemCount).firstOrNull { c.getMediaItemAt(it).mediaId == trackId }?.let { c.seekTo(it, 0); c.play() }
    }
    fun setShuffle(on: Boolean) { controller?.shuffleModeEnabled = on }
    fun cycleRepeat() {
        controller?.let {
            it.repeatMode = when (it.repeatMode) {
                Player.REPEAT_MODE_OFF -> Player.REPEAT_MODE_ALL
                Player.REPEAT_MODE_ALL -> Player.REPEAT_MODE_ONE
                else -> Player.REPEAT_MODE_OFF
            }
        }
    }

    private companion object {
        val NETWORK_ERRORS = setOf(
            PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED,
            PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT,
            PlaybackException.ERROR_CODE_IO_BAD_HTTP_STATUS,
        )
    }
}
