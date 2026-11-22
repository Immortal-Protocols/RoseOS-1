package dev.rose.music

import android.content.Context
import android.net.Uri
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

sealed interface DownloadState {
    data object None : DownloadState
    data object Queued : DownloadState
    data class Running(val progress: Float) : DownloadState
    data object Saved : DownloadState
    data class Failed(val reason: String) : DownloadState
}

sealed interface LibraryStatus {
    data object Loading : LibraryStatus
    data object Live : LibraryStatus
    /** The hosting answered but isn't serving right now (e.g. Chariot agent asleep: 503). */
    data object Waking : LibraryStatus
    data class Offline(val reason: String) : LibraryStatus
}

/**
 * The library hosted on the Chariot agent: the catalog (cached for offline use), downloads into app storage
 * (resumable, verified against the catalog's sha256), and where each track plays from.
 */
class Library(context: Context, val baseUrl: String = BuildConfig.CATALOG_BASE) {
    private val app = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val trackDir = File(app.filesDir, "tracks").apply { mkdirs() }
    private val catalogFile = File(app.filesDir, "catalog.json")
    private val gate = Semaphore(2)
    private companion object { const val CHUNK = 1L shl 20 }
    private val jobs = mutableMapOf<String, Job>()

    private val _catalog = MutableStateFlow(loadCached())
    val catalog: StateFlow<Catalog?> = _catalog.asStateFlow()
    private val _status = MutableStateFlow<LibraryStatus>(LibraryStatus.Loading)
    val status: StateFlow<LibraryStatus> = _status.asStateFlow()
    private val _downloads = MutableStateFlow<Map<String, DownloadState>>(emptyMap())
    val downloads: StateFlow<Map<String, DownloadState>> = _downloads.asStateFlow()

    init { _catalog.value?.let { syncSaved(it) } }

    fun url(path: String) = "$baseUrl/$path"
    fun localFile(t: Track) = File(trackDir, "${t.id}-${t.sha256.take(12)}.mp3")
    fun isSaved(t: Track) = localFile(t).isFile
    fun stateOf(t: Track) = _downloads.value[t.id] ?: DownloadState.None

    /** Saved copy first, so a downloaded track plays with no network. */
    fun playUri(t: Track): Uri = if (isSaved(t)) Uri.fromFile(localFile(t)) else Uri.parse(url(t.audio))

    fun canPlay(t: Track) = isSaved(t) || _status.value == LibraryStatus.Live

    private fun loadCached(): Catalog? = try {
        if (catalogFile.isFile) parseCatalog(catalogFile.readText()) else null
    } catch (_: Exception) { null }

    private fun syncSaved(c: Catalog) {
        _downloads.update { old ->
            c.tracks.associate { t ->
                t.id to when {
                    isSaved(t) -> DownloadState.Saved
                    old[t.id] is DownloadState.Running || old[t.id] is DownloadState.Queued -> old.getValue(t.id)
                    else -> DownloadState.None
                }
            }
        }
    }

    suspend fun refresh() = withContext(Dispatchers.IO) {
        if (_catalog.value == null) _status.value = LibraryStatus.Loading
        try {
            val conn = (URL(url("catalog.json")).openConnection() as HttpURLConnection).apply {
                connectTimeout = 10_000; readTimeout = 15_000
                setRequestProperty("Cache-Control", "no-cache")
            }
            try {
                when (val code = conn.responseCode) {
                    200 -> {
                        val text = conn.inputStream.bufferedReader().readText()
                        val c = parseCatalog(text)
                        File(catalogFile.path + ".tmp").apply { writeText(text); renameTo(catalogFile) }
                        _catalog.value = c
                        syncSaved(c)
                        pruneStale(c)
                        _status.value = LibraryStatus.Live
                    }
                    502, 503, 504 -> _status.value = LibraryStatus.Waking
                    else -> _status.value = LibraryStatus.Offline("HTTP $code")
                }
            } finally { conn.disconnect() }
        } catch (e: CatalogException) {
            _status.value = LibraryStatus.Offline("library unreadable")
        } catch (e: IOException) {
            _status.value = LibraryStatus.Offline("no connection")
        }
    }

    /** Files from tracks no longer in the catalog (or replaced by a new version) are removed. */
    private fun pruneStale(c: Catalog) {
        val keep = c.tracks.map { localFile(it).name }.toSet()
        trackDir.listFiles()?.forEach { f ->
            if (f.name.endsWith(".mp3") && f.name !in keep) f.delete()
        }
    }

    fun download(t: Track) {
        if (isSaved(t) || jobs[t.id]?.isActive == true) return
        set(t, DownloadState.Queued)
        jobs[t.id] = scope.launch {
            gate.withPermit {
                var attempt = 0
                while (true) {
                    try {
                        fetch(t)
                        set(t, DownloadState.Saved)
                        return@withPermit
                    } catch (e: IOException) {
                        ensureActive()
                        if (++attempt >= 6) { set(t, DownloadState.Failed(e.message ?: "failed")); return@withPermit }
                        delay(1000L * attempt)
                    }
                }
            }
        }.also { job -> job.invokeOnCompletion { if (it != null && !isSaved(t)) set(t, DownloadState.None) } }
    }

    fun downloadAll() = _catalog.value?.tracks?.forEach { download(it) }

    fun cancel(t: Track) {
        jobs.remove(t.id)?.cancel()
        File(trackDir, "${t.id}.part").delete()
        set(t, DownloadState.None)
    }

    fun remove(t: Track) {
        cancel(t)
        localFile(t).delete()
        set(t, DownloadState.None)
    }

    private fun set(t: Track, s: DownloadState) = _downloads.update { it + (t.id to s) }

    /**
     * Downloads in ranged pieces of at most 1 MiB: Chariot's gateway refuses larger single responses. A partial
     * file resumes where it stopped.
     */
    private suspend fun fetch(t: Track) = withContext(Dispatchers.IO) {
        val part = File(trackDir, "${t.id}.part")
        if (part.length() > t.bytes) part.delete()
        var done = part.length()
        set(t, DownloadState.Running(done.toFloat() / t.bytes))
        var lastEmit = 0L
        FileOutputStream(part, true).use { out ->
            while (done < t.bytes) {
                ensureActive()
                val last = minOf(done + CHUNK - 1, t.bytes - 1)
                val conn = (URL(url(t.audio)).openConnection() as HttpURLConnection).apply {
                    connectTimeout = 10_000; readTimeout = 20_000
                    setRequestProperty("Range", "bytes=$done-$last")
                }
                try {
                    val code = conn.responseCode
                    if (code != 206) throw IOException(if (code == 503) "library busy" else "HTTP $code")
                    conn.inputStream.use { input ->
                        val buf = ByteArray(64 * 1024)
                        while (true) {
                            ensureActive()
                            val n = input.read(buf)
                            if (n < 0) break
                            out.write(buf, 0, n)
                            done += n
                            val now = System.nanoTime()
                            if (now - lastEmit > 40_000_000) {
                                lastEmit = now
                                set(t, DownloadState.Running((done.toFloat() / t.bytes).coerceIn(0f, 1f)))
                            }
                        }
                    }
                    out.flush()
                } finally { conn.disconnect() }
            }
        }
        if (part.length() != t.bytes) throw IOException("incomplete")
        if (sha256(part) != t.sha256) { part.delete(); throw IOException("checksum mismatch") }
        if (!part.renameTo(localFile(t))) throw IOException("could not save")
    }

    private fun sha256(f: File): String {
        val md = MessageDigest.getInstance("SHA-256")
        f.inputStream().use { s ->
            val buf = ByteArray(64 * 1024)
            while (true) { val n = s.read(buf); if (n < 0) break; md.update(buf, 0, n) }
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }
}
