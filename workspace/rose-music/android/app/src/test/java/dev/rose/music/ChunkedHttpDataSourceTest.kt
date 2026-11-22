package dev.rose.music

import android.net.Uri
import androidx.media3.common.C
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.HttpDataSource
import androidx.media3.datasource.TransferListener
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.ByteArrayOutputStream
import kotlin.random.Random

/** A fake gateway: serves ranges of [body] and refuses, like Chariot's, any single response over [cap] bytes. */
@UnstableApi
private class FakeGateway(val body: ByteArray, val cap: Int = 2 * 1024 * 1024) : HttpDataSource {
    val requests = mutableListOf<LongRange>()
    private var data = ByteArray(0)
    private var at = 0
    private var headers = emptyMap<String, List<String>>()
    override fun open(dataSpec: DataSpec): Long {
        val from = dataSpec.position
        val to = if (dataSpec.length == C.LENGTH_UNSET.toLong()) body.size - 1L else minOf(body.size - 1L, from + dataSpec.length - 1)
        requests += from..to
        val n = (to - from + 1).toInt()
        if (n > cap) throw HttpDataSource.InvalidResponseCodeException(503, null, null, emptyMap(), dataSpec, ByteArray(0))
        data = body.copyOfRange(from.toInt(), to.toInt() + 1); at = 0
        headers = mapOf("content-range" to listOf("bytes $from-$to/${body.size}"))
        return n.toLong()
    }
    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        if (at >= data.size) return C.RESULT_END_OF_INPUT
        val n = minOf(length, data.size - at); System.arraycopy(data, at, buffer, offset, n); at += n; return n
    }
    override fun close() {}
    override fun getUri(): Uri? = null
    override fun getResponseHeaders() = headers
    override fun addTransferListener(transferListener: TransferListener) {}
    override fun setRequestProperty(name: String, value: String) {}
    override fun clearRequestProperty(name: String) {}
    override fun clearAllRequestProperties() {}
    override fun getResponseCode() = 206
}

@UnstableApi
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class ChunkedHttpDataSourceTest {
    private val body = Random(1).nextBytes(4_416_812) // the size of the longest song

    private fun readAll(src: ChunkedHttpDataSource, spec: DataSpec): Pair<Long, ByteArray> {
        val len = src.open(spec)
        val out = ByteArrayOutputStream(); val buf = ByteArray(70_000)
        while (true) { val n = src.read(buf, 0, buf.size); if (n == C.RESULT_END_OF_INPUT) break; out.write(buf, 0, n) }
        src.close()
        return len to out.toByteArray()
    }

    @Test fun readsAWholeSongInPiecesTheGatewayAccepts() {
        val gw = FakeGateway(body)
        val (len, bytes) = readAll(ChunkedHttpDataSource(gw), DataSpec(Uri.parse("https://x/tracks/10.mp3")))
        assertEquals(body.size.toLong(), len)
        assertArrayEquals(body, bytes)
        assertTrue(gw.requests.all { it.last - it.first + 1 <= ChunkedHttpDataSource.CHUNK_BYTES })
        assertEquals(5, gw.requests.size)
    }

    @Test fun seeksAndBoundedReads() {
        val gw = FakeGateway(body)
        val (len, bytes) = readAll(ChunkedHttpDataSource(gw), DataSpec.Builder().setUri("https://x/a").setPosition(1_500_000).setLength(1_800_000).build())
        assertEquals(1_800_000L, len)
        assertArrayEquals(body.copyOfRange(1_500_000, 3_300_000), bytes)
        val (len2, tail) = readAll(ChunkedHttpDataSource(gw), DataSpec.Builder().setUri("https://x/a").setPosition(4_000_000).build())
        assertEquals(416_812L, len2)
        assertArrayEquals(body.copyOfRange(4_000_000, body.size), tail)
    }

    @Test fun parsesContentRangeTotals() {
        assertEquals(4416812L, ChunkedHttpDataSource.parseTotal(mapOf("Content-Range" to listOf("bytes 0-1048575/4416812"))))
        assertEquals(-1L, ChunkedHttpDataSource.parseTotal(mapOf("Content-Range" to listOf("bytes 0-1/*"))))
        assertEquals(-1L, ChunkedHttpDataSource.parseTotal(emptyMap()))
    }
}
