package dev.pixel.voice;

import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioTrack;
import android.os.SystemClock;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayDeque;
import java.util.Iterator;

/**
 * Local Rose's voice on the phone, with the browser player's semantics
 * (prototypes/voice-phone-demo/rose/web/audio.js):
 * duck / pause (hold the place) / stop (40 ms fade, then a {@code stopped} report and
 * an empty queue), and {@code played} reports every 100 ms per piece.
 *
 * Reports count what the speaker has actually played, from the AudioTrack's playback
 * head, not what was written into its buffer. Only piece samples are ever written (an
 * empty or held queue writes nothing and lets the track run dry), so the head maps
 * one to one onto the pieces in the order they were written.
 */
final class LocalRosePlayer {
    interface Reports {
        void report(JSONObject m);
    }

    static final int RATE = 24000;
    private static final int CHUNK = 240;  // 10 ms per write
    private static final long REPORT_MS = 100;
    private static final float STEP = 1f / (0.04f * RATE);  // 40 ms ramps

    private static final class Piece {
        final int seg;
        final short[] data;
        int pos;          // samples written
        int end;          // samples that will be written (less than data.length if cut)
        long start = -1;  // index of the first sample in the written stream

        Piece(int seg, short[] data) {
            this.seg = seg;
            this.data = data;
            this.end = data.length;
        }
    }

    private final Reports reports;
    private final Object lock = new Object();
    private final ArrayDeque<Piece> pending = new ArrayDeque<>();   // not fully written
    private final ArrayDeque<Piece> inflight = new ArrayDeque<>();  // written from, not reported done
    private float target = 1f, gain = 1f;
    private boolean stopping, holding, dropAll, busy;
    private long written, lastReport;
    private AudioTrack track;
    private Thread thread;
    private volatile boolean running;
    /** RMS of what was last written, for the voice page's speaking meter. */
    volatile float outputLevel;

    LocalRosePlayer(Reports reports) {
        this.reports = reports;
    }

    void start() {
        int min = AudioTrack.getMinBufferSize(RATE, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT);
        track = new AudioTrack.Builder()
                .setAudioAttributes(new AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build())
                .setAudioFormat(new AudioFormat.Builder()
                        .setSampleRate(RATE)
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .build())
                .setTransferMode(AudioTrack.MODE_STREAM)
                .setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY)
                .setBufferSizeInBytes(Math.max(min, CHUNK * 2 * 4))
                .build();
        track.play();
        running = true;
        thread = new Thread(this::loop, "local-rose-player");
        thread.setPriority(Thread.MAX_PRIORITY);
        thread.start();
    }

    void stopPlayer() {
        running = false;
        if (thread != null) {
            thread.interrupt();
            try { thread.join(500); } catch (InterruptedException ignored) { }
        }
        if (track != null) {
            track.pause();
            track.flush();
            track.release();
            track = null;
        }
    }

    void enqueue(int seg, short[] pcm) {
        synchronized (lock) {
            pending.add(new Piece(seg, pcm));
        }
    }

    /** duck / unduck / pause / resume / stop, as sent by the server. */
    void control(JSONObject m) {
        String type = m.optString("type");
        synchronized (lock) {
            switch (type) {
                case "duck":
                    if (!stopping) target = (float) m.optDouble("gain", 0.25);
                    break;
                case "unduck":
                    if (!stopping && !holding) target = 1f;
                    break;
                case "pause":
                    if (!stopping) { holding = true; target = 0f; }
                    break;
                case "resume":
                    holding = false;
                    if (!stopping) target = 1f;
                    break;
                case "stop":
                    stopping = true;
                    target = 0f;
                    break;
                default:
                    break;
            }
        }
    }

    /** The server is gone: forget everything without reports (it already counts it as stopped). */
    void dropAll() {
        synchronized (lock) {
            dropAll = true;
        }
    }

    private void loop() {
        short[] chunk = new short[CHUNK];
        while (running) {
            int n = 0;
            boolean cut = false;
            synchronized (lock) {
                if (dropAll) {
                    pending.clear();
                    inflight.clear();
                    dropAll = stopping = holding = busy = false;
                    gain = target = 1f;
                }
                if (stopping && pending.isEmpty()) gain = 0f;
                while (n < CHUNK) {
                    if (gain < target) gain = Math.min(target, gain + STEP);
                    else if (gain > target) gain = Math.max(target, gain - STEP);
                    if (stopping && gain == 0f) { cut = true; break; }
                    Piece p = pending.peek();
                    if (p == null || (holding && gain == 0f)) break;
                    if (p.start < 0) {
                        p.start = written + n;
                        inflight.add(p);
                        busy = true;
                    }
                    chunk[n++] = (short) (p.data[p.pos++] * gain);
                    if (p.pos >= p.end) pending.poll();
                }
                if (cut) {
                    // Nothing more of this reply is written; the piece being played ends here.
                    Piece p = pending.peek();
                    if (p != null && p.start >= 0) p.end = p.pos;
                    for (Piece q : pending) if (q.start < 0) q.end = 0;
                    pending.clear();
                }
            }
            if (n > 0) {
                double sum = 0;
                for (int i = 0; i < n; i++) sum += (double) chunk[i] * chunk[i];
                outputLevel = (float) (Math.sqrt(sum / n) / 32768.0);
                track.write(chunk, 0, n, AudioTrack.WRITE_BLOCKING);
                written += n;
            } else {
                outputLevel = 0f;
            }
            if (cut) {
                finishStop();
                continue;
            }
            report(false);
            if (n == 0) SystemClock.sleep(5);
        }
    }

    private long head() {
        return track.getPlaybackHeadPosition() & 0xffffffffL;
    }

    /** played / done reports for whatever the speaker has played since the last call. */
    private void report(boolean force) {
        long head = head();
        long now = SystemClock.uptimeMillis();
        synchronized (lock) {
            Iterator<Piece> it = inflight.iterator();
            while (it.hasNext()) {
                Piece p = it.next();
                long played = Math.max(0, Math.min(p.end, head - p.start));
                boolean complete = p.end == p.data.length && p.pos >= p.end;
                if (complete && played >= p.end) {
                    send("played", p.seg, p.end, true);
                    it.remove();
                    lastReport = now;
                    continue;
                }
                if ((force || now - lastReport >= REPORT_MS) && played > 0) {
                    send("played", p.seg, (int) played, false);
                    lastReport = now;
                }
                break;
            }
            if (busy && inflight.isEmpty() && pending.isEmpty()) {
                busy = false;
                send("idle", -1, 0, false);
            }
        }
    }

    /** After the fade has been written: wait until it is heard, then report where the voice stopped. */
    private void finishStop() {
        long cutAt = written;
        long deadline = SystemClock.uptimeMillis() + 600;
        while (head() < cutAt && SystemClock.uptimeMillis() < deadline && running) {
            report(false);
            SystemClock.sleep(4);
        }
        report(true);
        synchronized (lock) {
            Piece last = inflight.peekLast();
            if (last != null) send("stopped", last.seg, last.pos, false);
            else send("stopped", -1, 0, false);
            inflight.clear();
            pending.clear();
            stopping = holding = busy = false;
            gain = target = 1f;
        }
    }

    private void send(String type, int seg, int samples, boolean done) {
        try {
            JSONObject m = new JSONObject().put("type", type);
            if (!type.equals("idle")) {
                m.put("seg", seg).put("samples", samples);
                if (type.equals("played")) m.put("done", done);
            }
            reports.report(m);
        } catch (JSONException ignored) {
        }
    }
}
