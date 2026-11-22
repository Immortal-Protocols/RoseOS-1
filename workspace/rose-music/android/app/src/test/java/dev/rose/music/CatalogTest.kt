package dev.rose.music

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Test

class CatalogTest {
    private val sha = "a".repeat(64)

    private fun track(id: String = "t01", audio: String = "\"tracks/01.mp3\"", sha256: String = sha, cover: String = "\"covers/01.jpg\"") = """
        {"id":"$id","number":1,"title":"Glass Orchard","artist":"Rose Music Ensemble","durationMs":52000,"bpm":140,
         "key":"E major","audio":$audio,"bytes":832940,"sha256":"$sha256","cover":$cover,"thumb":null,"peaks":[0,0.5,1,2]}
    """

    private fun catalog(vararg tracks: String) =
        """{"schema":"rose-music-catalog/v1","name":"Rose Music","album":"Glass Orchard","publishedAt":1,"tracks":[${tracks.joinToString(",")}]}"""

    @Test fun parsesAPublishedCatalog() {
        val c = parseCatalog(catalog(track()))
        assertEquals("Glass Orchard", c.album)
        val t = c.tracks.single()
        assertEquals("tracks/01.mp3", t.audio)
        assertEquals("covers/01.jpg", t.cover)
        assertNull(t.thumb)
        assertEquals(52000L, t.durationMs)
        // Peaks are clamped into 0..1.
        assertEquals(listOf(0f, 0.5f, 1f, 1f), t.peaks.toList())
    }

    @Test fun refusesAnotherSchema() {
        assertThrows(CatalogException::class.java) { parseCatalog(catalog(track()).replace("rose-music-catalog/v1", "v2")) }
    }

    @Test fun refusesPathsThatLeaveTheLibrary() {
        for (bad in listOf("\"../etc/passwd\"", "\"https://elsewhere.example/a.mp3\"", "\"/tracks/01.mp3\"", "\"tracks/../x.mp3\"")) {
            assertThrows(bad, CatalogException::class.java) { parseCatalog(catalog(track(audio = bad))) }
        }
    }

    @Test fun refusesABadChecksumAndDuplicateIds() {
        assertThrows(CatalogException::class.java) { parseCatalog(catalog(track(sha256 = "xyz"))) }
        assertThrows(CatalogException::class.java) { parseCatalog(catalog(track(), track())) }
    }

    @Test fun refusesNonJson() {
        assertThrows(CatalogException::class.java) { parseCatalog("<html>503</html>") }
    }

    @Test fun formatsTimes() {
        assertEquals("3:16", formatDuration(196_400))
        assertEquals("52 sec", formatTotal(52_000))
        assertEquals("25 min", formatTotal(25 * 60_000L + 5_000))
        assertEquals("1 hr 2 min", formatTotal(62 * 60_000L))
    }
}
