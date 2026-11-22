package dev.rose.music

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color as AColor
import android.util.LruCache
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.State
import androidx.compose.runtime.produceState
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/** Colours taken from a cover: a vivid accent for highlights, a deep tone for backgrounds, and a glow. */
@Immutable
data class CoverColors(val accent: Color, val deep: Color, val glow: Color) {
    companion object {
        val Default = CoverColors(Color(0xFFB9A2FF), Color(0xFF17122A), Color(0xFF6E5BD6))
    }
}

class LoadedCover(val image: ImageBitmap, val colors: CoverColors)

/** Covers are downloaded once into the cache and kept decoded in memory. */
object Covers {
    private val memory = object : LruCache<String, LoadedCover>(48 * 1024 * 1024) {
        override fun sizeOf(key: String, value: LoadedCover) = value.image.width * value.image.height * 4
    }
    private val locks = mutableMapOf<String, Mutex>()

    /** The library's base URL; catalog paths (covers/NN.jpg) are relative to it. */
    @Volatile var base: String = BuildConfig.CATALOG_BASE

    private fun resolve(path: String) = if (path.startsWith("https://")) path else "$base/$path"

    fun cached(url: String?): LoadedCover? = url?.let { memory.get(it) }

    suspend fun load(context: Context, url: String): LoadedCover? = withContext(Dispatchers.IO) {
        memory.get(url)?.let { return@withContext it }
        val lock = synchronized(locks) { locks.getOrPut(url) { Mutex() } }
        lock.withLock {
            memory.get(url)?.let { return@withLock it }
            // Kept in app files (not cache) so the library looks the same offline.
            val file = File(File(context.filesDir, "covers").apply { mkdirs() }, url.hashCode().toUInt().toString(16) + ".jpg")
            if (!file.isFile && !fetch(url, file)) return@withLock null
            val bmp = BitmapFactory.decodeFile(file.path) ?: run { file.delete(); return@withLock null }
            LoadedCover(bmp.asImageBitmap(), extract(bmp)).also { memory.put(url, it) }
        }
    }

    // Chariot's gateway can answer 503 to bursts, so covers load a few at a time and retry with backoff.
    private val gate = kotlinx.coroutines.sync.Semaphore(3)

    private suspend fun fetch(url: String, file: File): Boolean = gate.withPermit {
        for (attempt in 0 until 5) {
            try {
                val c = URL(resolve(url)).openConnection() as HttpURLConnection
                c.connectTimeout = 10_000; c.readTimeout = 15_000
                try {
                    when (c.responseCode) {
                        200 -> {
                            val tmp = File(file.path + ".tmp")
                            c.inputStream.use { i -> tmp.outputStream().use { o -> i.copyTo(o) } }
                            return@withPermit tmp.renameTo(file)
                        }
                        429, 502, 503, 504 -> {}
                        else -> return@withPermit false
                    }
                } finally { c.disconnect() }
            } catch (_: java.io.IOException) {}
            kotlinx.coroutines.delay(400L shl attempt)
        }
        false
    }

    /** A small, dependency-free palette: the most vivid bright pixel is the accent, a darkened average the deep tone. */
    fun extract(src: Bitmap): CoverColors {
        val s = Bitmap.createScaledBitmap(src, 32, 32, true)
        val hsv = FloatArray(3)
        var best = 0f; var accent = AColor.rgb(185, 162, 255)
        var r = 0L; var g = 0L; var b = 0L
        for (y in 0 until 32) for (x in 0 until 32) {
            val p = s.getPixel(x, y)
            r += AColor.red(p); g += AColor.green(p); b += AColor.blue(p)
            AColor.colorToHSV(p, hsv)
            val score = hsv[1] * hsv[2] * (if (hsv[2] > 0.45f) 1.2f else 0.6f)
            if (score > best) { best = score; accent = p }
        }
        if (s != src) s.recycle()
        AColor.colorToHSV(accent, hsv)
        hsv[1] = hsv[1].coerceIn(0.35f, 0.8f); hsv[2] = hsv[2].coerceIn(0.82f, 1f)
        val acc = AColor.HSVToColor(hsv)
        AColor.colorToHSV(AColor.rgb((r / 1024).toInt(), (g / 1024).toInt(), (b / 1024).toInt()), hsv)
        hsv[1] = (hsv[1] * 1.1f).coerceIn(0.2f, 0.7f); hsv[2] = 0.16f
        val deep = AColor.HSVToColor(hsv)
        AColor.colorToHSV(accent, hsv); hsv[2] = 0.65f; hsv[1] = hsv[1].coerceIn(0.4f, 0.9f)
        return CoverColors(Color(acc), Color(deep), Color(AColor.HSVToColor(hsv)))
    }
}

@Composable
fun rememberCover(url: String?): State<LoadedCover?> {
    val ctx = LocalContext.current
    return produceState(Covers.cached(url), url) {
        value = url?.let { Covers.load(ctx, it) }
    }
}
