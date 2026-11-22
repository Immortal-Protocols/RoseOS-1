package dev.rose.music

import android.net.Uri
import androidx.media3.common.C
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.HttpDataSource
import androidx.media3.datasource.TransferListener

/**
 * Chariot's agent-app gateway refuses (503) any single response larger than about 2 MiB, so a song can't be
 * streamed with one open-ended request. This reads an HTTP resource as consecutive ranged requests of at most
 * [chunk] bytes, while presenting one continuous stream to the player.
 */
@UnstableApi
class ChunkedHttpDataSource(private val upstream: HttpDataSource, private val chunk: Long = CHUNK_BYTES) : DataSource {
    private var spec: DataSpec? = null
    private var pos = 0L
    private var end = -1L // exclusive; -1 when the request is open-ended
    private var total = -1L
    private var open = false

    override fun addTransferListener(transferListener: TransferListener) = upstream.addTransferListener(transferListener)

    override fun open(dataSpec: DataSpec): Long {
        spec = dataSpec
        pos = dataSpec.position
        end = if (dataSpec.length != C.LENGTH_UNSET.toLong()) pos + dataSpec.length else -1L
        total = -1L
        openChunk()
        return when {
            end >= 0 -> end - dataSpec.position
            total >= 0 -> total - dataSpec.position
            else -> C.LENGTH_UNSET.toLong()
        }
    }

    private fun openChunk() {
        val s = spec!!
        val want = if (end >= 0) minOf(chunk, end - pos) else chunk
        upstream.open(s.subrange(pos - s.position, want))
        open = true
        if (total < 0) total = parseTotal(upstream.responseHeaders)
    }

    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        if (length == 0) return 0
        if (end in 0..pos) return C.RESULT_END_OF_INPUT
        var n = upstream.read(buffer, offset, length)
        if (n == C.RESULT_END_OF_INPUT) {
            upstream.close(); open = false
            if (total < 0 || pos >= total || (end >= 0 && pos >= end)) return C.RESULT_END_OF_INPUT
            openChunk()
            n = upstream.read(buffer, offset, length)
            if (n == C.RESULT_END_OF_INPUT) return n
        }
        pos += n
        return n
    }

    override fun getUri(): Uri? = upstream.uri ?: spec?.uri
    override fun getResponseHeaders(): Map<String, List<String>> = upstream.responseHeaders

    override fun close() {
        if (open) { open = false; upstream.close() }
    }

    companion object {
        const val CHUNK_BYTES = 1L shl 20

        /** The total from `Content-Range: bytes a-b/total`, or -1. */
        fun parseTotal(headers: Map<String, List<String>>): Long {
            val v = headers.entries.firstOrNull { it.key.equals("Content-Range", ignoreCase = true) }?.value?.firstOrNull()
                ?: return -1L
            return v.substringAfterLast('/', "").trim().toLongOrNull() ?: -1L
        }
    }
}
