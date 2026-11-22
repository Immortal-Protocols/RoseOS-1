package dev.rose.music

import org.json.JSONObject

/** One song in the library, as `rose-music-catalog/v1` describes it (see hosting/publish.py). */
data class Track(
    val id: String,
    val number: Int,
    val title: String,
    val artist: String,
    val durationMs: Long,
    val bpm: Int,
    val key: String,
    val audio: String,
    val bytes: Long,
    val sha256: String,
    val cover: String?,
    val thumb: String?,
    val peaks: FloatArray,
) {
    override fun equals(other: Any?) = other is Track && other.id == id && other.sha256 == sha256
    override fun hashCode() = id.hashCode() * 31 + sha256.hashCode()
}

data class Catalog(
    val name: String,
    val album: String,
    val publishedAt: Long,
    val tracks: List<Track>,
) {
    val totalDurationMs get() = tracks.sumOf { it.durationMs }
    val totalBytes get() = tracks.sumOf { it.bytes }
    fun byId(id: String?) = tracks.firstOrNull { it.id == id }
}

class CatalogException(message: String) : Exception(message)

private val SAFE_PATH = Regex("^[a-z0-9]+(/[a-z0-9._-]+)*$")
private val SHA256 = Regex("^[0-9a-f]{64}$")

/** Parses and checks a catalog. Paths are relative to the library's own base URL and never absolute. */
fun parseCatalog(text: String): Catalog {
    val o = try { JSONObject(text) } catch (e: Exception) { throw CatalogException("not JSON") }
    if (o.optString("schema") != "rose-music-catalog/v1") throw CatalogException("unknown schema")
    val arr = o.optJSONArray("tracks") ?: throw CatalogException("no tracks")
    val tracks = (0 until arr.length()).map { i ->
        val t = arr.getJSONObject(i)
        fun path(name: String, required: Boolean): String? {
            if (t.isNull(name)) { if (required) throw CatalogException("track $i: $name missing"); return null }
            val p = t.getString(name)
            if (!SAFE_PATH.matches(p) || p.contains("..")) throw CatalogException("track $i: bad $name")
            return p
        }
        val peaksJson = t.optJSONArray("peaks")
        val peaks = FloatArray(peaksJson?.length() ?: 0) { peaksJson!!.optDouble(it, 0.0).toFloat().coerceIn(0f, 1f) }
        val sha = t.getString("sha256")
        if (!SHA256.matches(sha)) throw CatalogException("track $i: bad sha256")
        val id = t.getString("id")
        if (!Regex("^[a-z0-9]{1,32}$").matches(id)) throw CatalogException("track $i: bad id")
        Track(
            id = id,
            number = t.optInt("number", i + 1),
            title = t.getString("title"),
            artist = t.optString("artist", ""),
            durationMs = t.getLong("durationMs"),
            bpm = t.optInt("bpm", 0),
            key = t.optString("key", ""),
            audio = path("audio", true)!!,
            bytes = t.getLong("bytes"),
            sha256 = sha,
            cover = path("cover", false),
            thumb = path("thumb", false),
            peaks = peaks,
        )
    }
    if (tracks.map { it.id }.toSet().size != tracks.size) throw CatalogException("duplicate track ids")
    return Catalog(
        name = o.optString("name", "Rose Music"),
        album = o.optString("album", ""),
        publishedAt = o.optLong("publishedAt", 0),
        tracks = tracks,
    )
}

fun formatDuration(ms: Long): String {
    val s = (ms / 1000).coerceAtLeast(0)
    return "%d:%02d".format(s / 60, s % 60)
}

fun formatTotal(ms: Long): String {
    val m = ms / 60000
    if (m == 0L) return "${ms / 1000} sec"
    return if (m >= 60) "${m / 60} hr ${m % 60} min" else "$m min"
}

fun formatBytes(b: Long): String = when {
    b >= 1_000_000_000 -> "%.1f GB".format(b / 1e9)
    b >= 1_000_000 -> "%.0f MB".format(b / 1e6)
    else -> "%.0f KB".format(b / 1e3)
}
