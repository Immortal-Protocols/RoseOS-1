package dev.rose.music

import android.Manifest
import android.app.Application
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.asPaddingValues
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.rose.music.ui.Aurora
import dev.rose.music.ui.Ink
import dev.rose.music.ui.LibraryActions
import dev.rose.music.ui.LibraryScreen
import dev.rose.music.ui.LocalCoverColors
import dev.rose.music.ui.MiniPlayer
import dev.rose.music.ui.PlayerActions
import dev.rose.music.ui.PlayerPane
import dev.rose.music.ui.RoseMusicTheme
import dev.rose.music.ui.Type
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class RoseMusicApp : Application() {
    val library by lazy { Library(this) }
    val player by lazy { PlayerConnection(this, library) }
}

class MainActivity : ComponentActivity() {
    private val notifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) {}

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge(SystemBarStyle.dark(android.graphics.Color.TRANSPARENT), SystemBarStyle.dark(android.graphics.Color.TRANSPARENT))
        super.onCreate(savedInstanceState)
        val app = application as RoseMusicApp
        setContent { RoseMusicTheme { Root(app.library, app.player, ::askForNotifications) } }
    }

    override fun onStart() { super.onStart(); (application as RoseMusicApp).player.connect() }
    override fun onStop() { (application as RoseMusicApp).player.release(); super.onStop() }

    private fun askForNotifications() {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) notifications.launch(Manifest.permission.POST_NOTIFICATIONS)
    }
}

@Composable
private fun Root(library: Library, player: PlayerConnection, beforeFirstPlay: () -> Unit) {
    val catalog by library.catalog.collectAsStateWithLifecycle()
    val status by library.status.collectAsStateWithLifecycle()
    val downloads by library.downloads.collectAsStateWithLifecycle()
    val ui by player.state.collectAsStateWithLifecycle()
    var expanded by rememberSaveable { mutableStateOf(false) }
    var toast by remember { mutableStateOf<String?>(null) }
    var askedNotifications by rememberSaveable { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        // Refresh now, and keep retrying quietly while the library is unreachable.
        while (true) {
            library.refresh()
            if (library.status.value == LibraryStatus.Live) break
            delay(15_000)
        }
    }
    LaunchedEffect(ui.error) { ui.error?.let { toast = it; player.clearError() } }
    LaunchedEffect(toast) { if (toast != null) { delay(2800); toast = null } }

    val current = catalog?.byId(ui.trackId)
    val lead = current ?: catalog?.tracks?.firstOrNull()
    val leadCover by rememberCover(lead?.cover)
    val target = leadCover?.colors ?: CoverColors.Default
    val accent by animateColorAsState(target.accent, tween(900), label = "accent")
    val deep by animateColorAsState(target.deep, tween(900), label = "deep")
    val glow by animateColorAsState(target.glow, tween(900), label = "glow")
    val colors = CoverColors(accent, deep, glow)

    fun queue(c: Catalog) = c.tracks.filter { library.canPlay(it) }
    fun play(t: Track) {
        val c = catalog ?: return
        if (!library.canPlay(t)) { toast = "“${t.title}” isn't saved. Connect to stream it."; return }
        if (!askedNotifications) { askedNotifications = true; beforeFirstPlay() }
        player.play(queue(c), t)
    }
    fun download(t: Track) {
        when (library.stateOf(t)) {
            DownloadState.None, is DownloadState.Failed -> {
                if (status != LibraryStatus.Live) toast = "Connect to the internet to save songs."
                else library.download(t)
            }
            DownloadState.Queued, is DownloadState.Running -> { library.cancel(t); toast = "Stopped saving “${t.title}”." }
            DownloadState.Saved -> toast = "Saved on this phone · hold to remove"
        }
    }
    val libraryActions = LibraryActions(
        play = ::play,
        playAll = { shuffle ->
            catalog?.let { c ->
                val q = queue(c)
                if (q.isEmpty()) toast = "No saved songs yet." else { player.setShuffle(shuffle); play(if (shuffle) q.random() else q.first()) }
            }
        },
        download = ::download,
        downloadAll = {
            if (status != LibraryStatus.Live) toast = "Connect to the internet to save songs." else library.downloadAll()
        },
        refresh = { library.refresh() },
        canPlay = { library.canPlay(it) },
        remove = { library.remove(it); toast = "Removed “${it.title}” from this phone." },
    )
    val playerActions = PlayerActions(
        toggle = player::toggle, next = player::next, previous = player::previous, seekTo = player::seekTo,
        skipTo = { player.skipTo(it.id) }, shuffle = player::setShuffle, cycleRepeat = player::cycleRepeat,
        download = ::download, remove = { library.remove(it) },
    )

    BackHandler(expanded) { expanded = false }

    CompositionLocalProvider(LocalCoverColors provides colors) {
        BoxWithConstraints(Modifier.fillMaxSize().background(Ink.Night).testTag("root")) {
            val wide = maxWidth >= 720.dp
            Aurora(colors.accent, colors.deep, colors.glow)
            val bars = WindowInsets.statusBars.asPaddingValues()
            val nav = WindowInsets.navigationBars.asPaddingValues()
            if (wide && catalog != null) {
                Row(Modifier.fillMaxSize()) {
                    LibraryScreen(
                        catalog, status, downloads, ui, libraryActions,
                        contentPadding = PaddingValues(top = bars.calculateTopPadding(), bottom = nav.calculateBottomPadding() + 24.dp),
                        modifier = Modifier.weight(1f),
                    )
                    Box(Modifier.width(0.75.dp).fillMaxHeight().background(Ink.Hairline))
                    Box(Modifier.weight(1f).fillMaxHeight()) {
                        val shown = current ?: catalog!!.tracks.first()
                        PlayerPane(
                            catalog!!, catalog!!.tracks, shown, if (current != null) ui else PlayerUi(trackId = null, durationMs = shown.durationMs),
                            downloads[shown.id] ?: DownloadState.None,
                            if (current != null) playerActions else PlayerActions(
                                toggle = { play(shown) }, next = { }, previous = { }, seekTo = { }, skipTo = { play(it) },
                                shuffle = player::setShuffle, cycleRepeat = player::cycleRepeat, download = ::download, remove = { library.remove(it) },
                            ),
                            onClose = null,
                        )
                    }
                }
            } else {
                LibraryScreen(
                    catalog, status, downloads, ui, libraryActions,
                    contentPadding = PaddingValues(top = bars.calculateTopPadding(), bottom = nav.calculateBottomPadding() + if (current != null) 112.dp else 24.dp),
                )
                AnimatedVisibility(
                    visible = current != null && !expanded,
                    enter = slideInVertically(spring(0.75f, 300f)) { it * 2 } + fadeIn(),
                    exit = slideOutVertically(tween(220)) { it * 2 } + fadeOut(tween(180)),
                    modifier = Modifier.align(Alignment.BottomCenter).navigationBarsPadding().padding(bottom = 10.dp),
                ) {
                    current?.let { MiniPlayer(it, ui, playerActions, onOpen = { expanded = true }) }
                }
                AnimatedVisibility(
                    visible = expanded && current != null && catalog != null,
                    enter = slideInVertically(spring(0.86f, 260f)) { it } + fadeIn(tween(160)),
                    exit = slideOutVertically(spring(0.9f, 320f)) { it } + fadeOut(tween(260)),
                ) {
                    val t = current ?: return@AnimatedVisibility
                    PlayerPane(catalog!!, queue(catalog!!), t, ui, downloads[t.id] ?: DownloadState.None, playerActions, onClose = { expanded = false })
                }
            }
            // Toast pill
            AnimatedVisibility(
                visible = toast != null,
                enter = slideInVertically(spring(0.6f, 420f)) { -it * 2 } + fadeIn(),
                exit = slideOutVertically(tween(200)) { -it * 2 } + fadeOut(),
                modifier = Modifier.align(Alignment.TopCenter).padding(top = bars.calculateTopPadding() + 10.dp),
            ) {
                var last by remember { mutableStateOf("") }
                toast?.let { last = it }
                Text(
                    last, style = Type.Label, color = Ink.Text,
                    modifier = Modifier.padding(horizontal = 24.dp).clip(RoundedCornerShape(50)).background(Ink.Raised.copy(alpha = 0.96f))
                        .border(0.75.dp, Color.White.copy(alpha = 0.1f), RoundedCornerShape(50)).padding(horizontal = 18.dp, vertical = 11.dp),
                )
            }
        }
    }
}
