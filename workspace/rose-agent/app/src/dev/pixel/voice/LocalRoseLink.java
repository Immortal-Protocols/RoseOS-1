package dev.pixel.voice;

import android.annotation.SuppressLint;
import android.content.Context;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.media.audiofx.AcousticEchoCanceler;
import android.media.audiofx.NoiseSuppressor;
import android.os.SystemClock;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

/**
 * Local Rose: every model runs on the Mac (prototypes/voice-phone-demo, `bash scripts/rose.sh`).
 * Once paired by QR code it is reached over Tailscale with pinned TLS and signed
 * requests (LocalRoseConnection). Unpaired phones wait for owner pairing.
 * No cloud provider or Rose Agent backend.
 *
 * This is the voice. Mic frames go up and Rose's voice comes down on /ws/audio (wire protocol
 * at the top of prototypes/voice-phone-demo/rose/web/audio.js); her turns come from /ws/events
 * for the voice page. The mic uses the voice-call path, so Android's echo canceller removes
 * Rose's own voice (played with voice-call usage) before it reaches the Mac. The voice page
 * routes audio (Native.audio) before starting this. Phone commands are LocalRosePhone.
 */
final class LocalRoseLink {
    /**
     * Events for the voice page: status (live with route, reconnecting, unreachable, unpaired, clock),
     * level, mode, message, task, closed.
     */
    interface Sink {
        void event(JSONObject m);
    }

    private static final int MIC_RATE = 16000;
    private static final int FRAME = 512;  // 32 ms
    private static final int REPLACED = 4001, AUDIO_OFF = 4003;

    private static volatile LocalRoseLink current;
    /** The last mute value asked for, kept even while no link is published (a start ending an older link). */
    private static volatile boolean requestedMute;

    private final Context context;
    private final Sink sink;
    private final LocalRosePlayer player;
    private volatile boolean running, muted, speaking;
    private volatile LocalRoseSocket audioWs, eventsWs;
    /** Every socket this link opened and has not finished with; end() closes them all, even late ones. */
    private final List<LocalRoseSocket> sockets = new ArrayList<>();
    private Thread audioThread, eventsThread, micThread;
    private AudioRecord rec;
    private AcousticEchoCanceler aec;
    private NoiseSuppressor ns;

    /** On unless the "local-rose" preference "enabled" is set false (then the cloud backend path runs). */
    static boolean enabled(Context c) {
        return c.getSharedPreferences("local-rose", 0).getBoolean("enabled", true);
    }

    private static int lastToken;
    /** This link's start token: its own start's stop(token) ends it, and no other start's does. */
    private int token;

    /**
     * Starts a link with exactly this mute (#410 item 6) and returns its token (always positive). A link already
     * running belongs to an earlier start, for example a voice page whose renderer is gone, so it is ended first: it
     * is never reused, and the new start's muted value and sink always apply. Muted, the first frame is silence.
     */
    static synchronized int start(Context context, Sink sink, boolean muted) {
        LocalRoseLink old = current;
        current = null;
        if (old != null) old.end(); // an earlier start's link is never reused; nothing else waits on the lock for it
        LocalRoseLink link = new LocalRoseLink(context, sink);
        link.muted = muted;
        link.token = lastToken = lastToken == Integer.MAX_VALUE ? 1 : lastToken + 1;
        current = link;
        // A mute asked for while no link was published (setMuted saw none) is applied before the first frame: setMuted
        // writes requestedMute then reads current, this writes current then reads requestedMute, so one sees the other.
        if (requestedMute) link.muted = true;
        link.begin();
        return link.token;
    }

    /** Ends the link its own start returned token for; an older start's stop (or token 0, no link) ends nothing. */
    static void stop(int token) {
        LocalRoseLink ended;
        synchronized (LocalRoseLink.class) {
            if (current == null || token <= 0 || current.token != token) return;
            ended = current;
            current = null;
        }
        ended.end(); // joins its threads without holding the lock that mute and silence would wait for
    }

    /** Ends whatever link runs: the voice page that owned it is gone (renderer loss, service end). */
    static void stop() {
        LocalRoseLink ended;
        synchronized (LocalRoseLink.class) {
            ended = current;
            current = null;
        }
        if (ended != null) ended.end();
    }

    /** Silences whatever link runs, from its next frame, and returns its token (0 if none) so it can be ended later.
     * Lock-free: never waits for a link that is starting or ending. */
    static int silence() {
        LocalRoseLink link = current;
        if (link == null) return 0;
        link.muted = true;
        return link.token;
    }

    /** Lock-free, so a mute from the main thread never waits for a link that is starting or ending. */
    static void setMuted(boolean muted) {
        requestedMute = muted;
        LocalRoseLink link = current;
        if (link != null) link.muted = muted;
    }

    private LocalRoseLink(Context context, Sink sink) {
        this.context = context.getApplicationContext();
        this.sink = sink;
        player = new LocalRosePlayer(m -> {
            String type = m.optString("type");
            if (!type.equals("idle")) sendAudioText(m.toString());
            boolean now = type.equals("played") || speaking && !type.equals("idle") && !type.equals("stopped");
            if (now != speaking) {
                speaking = now;
                emit(obj("type", "mode", "mode", now ? "speaking" : "listening"));
            }
        });
    }

    private void begin() {
        running = true;
        player.start();
        startMic();
        audioThread = new Thread(this::audioLoop, "local-rose-audio");
        audioThread.start();
        eventsThread = new Thread(this::eventsLoop, "local-rose-events");
        eventsThread.start();
    }

    private void end() {
        List<LocalRoseSocket> open;
        synchronized (sockets) {
            running = false;
            open = new ArrayList<>(sockets);
        }
        for (LocalRoseSocket w : open) w.close();
        LocalRoseConnection.wake();  // ends a retry wait
        if (rec != null) rec.stop();
        join(micThread);
        join(audioThread);
        join(eventsThread);
        if (rec != null) rec.release();
        if (aec != null) aec.release();
        if (ns != null) ns.release();
        player.stopPlayer();
    }

    @SuppressLint("MissingPermission")  // the voice page starts this only after Native.audio with RECORD_AUDIO held
    private void startMic() {
        int min = AudioRecord.getMinBufferSize(MIC_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        rec = new AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, MIC_RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, Math.max(min, FRAME * 2 * 4));
        if (AcousticEchoCanceler.isAvailable()) {
            aec = AcousticEchoCanceler.create(rec.getAudioSessionId());
            if (aec != null) aec.setEnabled(true);
        }
        if (NoiseSuppressor.isAvailable()) {
            ns = NoiseSuppressor.create(rec.getAudioSessionId());
            if (ns != null) ns.setEnabled(true);
        }
        rec.startRecording();
        micThread = new Thread(this::micLoop, "local-rose-mic");
        micThread.setPriority(Thread.MAX_PRIORITY);
        micThread.start();
    }

    private void micLoop() {
        short[] pcm = new short[FRAME];
        byte[] bytes = new byte[FRAME * 2];
        int frames = 0;
        while (running) {
            int got = 0;
            while (got < FRAME && running) {
                int r = rec.read(pcm, got, FRAME - got);
                if (r < 0) {
                    SystemClock.sleep(20);
                    break;
                }
                got += r;
            }
            if (got < FRAME) continue;
            double sum = 0;
            boolean silent = muted;  // muted: keep Rose's clock running with silence
            for (int i = 0; i < FRAME; i++) {
                short s = silent ? 0 : pcm[i];
                bytes[2 * i] = (byte) s;
                bytes[2 * i + 1] = (byte) (s >> 8);
                double f = s / 32768.0;
                sum += f * f;
            }
            LocalRoseSocket w = audioWs;
            if (w != null) {
                try {
                    w.sendBinary(bytes, 0, bytes.length);
                } catch (IOException e) {
                    w.close();
                }
            }
            if (++frames % 2 == 0) {
                try {
                    emit(new JSONObject().put("type", "level").put("input", Math.sqrt(sum / FRAME))
                            .put("output", player.outputLevel));
                } catch (JSONException ignored) {
                }
            }
        }
    }

    /**
     * Takes a socket that open() returned. False (and the socket is closed) when the link was
     * stopped while it was connecting, so a late connection never outlives its session.
     */
    private boolean adopt(LocalRoseSocket w) {
        synchronized (sockets) {
            if (running) {
                sockets.add(w);
                return true;
            }
        }
        w.close();
        LocalRoseConnection.closed(w);
        return false;
    }

    private void release(LocalRoseSocket w) {
        synchronized (sockets) {
            sockets.remove(w);
        }
        LocalRoseConnection.closed(w);
    }

    private void audioLoop() {
        boolean wasLive = false;
        LocalRoseConnection.Retry retry = new LocalRoseConnection.Retry();
        while (running) {
            LocalRoseSocket w = null;
            String route;
            retry.attempt();
            try {
                w = LocalRoseConnection.open(context, "/ws/audio");
                if (!adopt(w)) break;
                route = LocalRoseConnection.routeOf(w);
                w.sendText(new JSONObject().put("type", "hello").put("captureRate", MIC_RATE)
                        .put("playRate", LocalRosePlayer.RATE).put("client", "rose-agent").toString());
            } catch (LocalRoseConnection.Unpaired e) {
                emit(obj("type", "status", "status", "unpaired"));
                retry.parked();
                continue;
            } catch (LocalRoseConnection.ClockOff e) {
                emit(obj("type", "status", "status", "clock"));
                retry.failed();
                continue;
            } catch (IOException | JSONException e) {
                if (w != null) {
                    w.close();
                    release(w);
                }
                emit(obj("type", "status", "status", wasLive ? "reconnecting" : "unreachable"));
                retry.failed();
                continue;
            }
            audioWs = w;
            wasLive = true;
            retry.connected();
            emit(obj("type", "status", "status", "live", "route", route));
            w.readLoop(new LocalRoseSocket.Listener() {
                @Override
                public void onText(String text) {
                    try {
                        player.control(new JSONObject(text));
                    } catch (JSONException ignored) {
                    }
                }

                @Override
                public void onBinary(byte[] data) {
                    if (data.length < 6) return;
                    int seg = (data[0] & 0xff) | (data[1] & 0xff) << 8 | (data[2] & 0xff) << 16 | (data[3] & 0xff) << 24;
                    short[] pcm = new short[(data.length - 4) / 2];
                    for (int i = 0; i < pcm.length; i++) pcm[i] = (short) ((data[4 + 2 * i] & 0xff) | (data[5 + 2 * i] << 8));
                    player.enqueue(seg, pcm);
                }
            });
            audioWs = null;
            release(w);
            player.dropAll();
            if (!running) break;
            if (w.closeCode == REPLACED || w.closeCode == AUDIO_OFF) {
                emit(obj("type", "closed", "code", String.valueOf(w.closeCode)));
                running = false;
                break;
            }
            emit(obj("type", "status", "status", "reconnecting"));
            retry.failed();
        }
    }

    /** Rose's finished turns, for the voice page's transcript (the history replayed on connect is skipped). */
    private void eventsLoop() {
        LocalRoseConnection.Retry retry = new LocalRoseConnection.Retry();
        while (running) {
            LocalRoseSocket w;
            retry.attempt();
            final String identityScope = LocalAssistantIdentity.scope(context);
            try {
                w = LocalRoseConnection.open(context, "/ws/events");
                if (!identityScope.equals(LocalAssistantIdentity.scope(context))) {
                    w.close(); LocalRoseConnection.closed(w); continue;
                }
                if (!adopt(w)) break;
            } catch (LocalRoseConnection.Unpaired e) {
                retry.parked();  // the audio loop tells the page
                continue;
            } catch (IOException e) {
                retry.failed();
                continue;
            }
            eventsWs = w;
            retry.connected();
            w.readLoop(new LocalRoseSocket.Listener() {
                @Override
                public void onText(String text) {
                    try {
                        JSONObject m = new JSONObject(text);
                        // Use the link's end/adopt lock, not the global start lock: start() joins an
                        // older event thread while holding the latter, so callbacks must not wait on it.
                        synchronized (sockets) {
                            if (current != LocalRoseLink.this || !running) return;
                            LocalAssistantIdentity.accept(context, identityScope, m);
                        }
                        String kind = m.optString("kind");
                        if (m.optBoolean("replay")) return;
                        if (kind.equals("user_turn") && !m.optBoolean("hold")) {
                            emit(new JSONObject().put("type", "message").put("role", "user")
                                    .put("id", m.optInt("id")).put("text", m.optString("text")));
                        } else if (kind.equals("rose_turn")) {
                            emit(new JSONObject().put("type", "message").put("role", "agent")
                                    .put("id", m.optInt("id")).put("text", m.optString("text")));
                        } else if (kind.equals("task")) {
                            emit(new JSONObject().put("type", "task").put("task", m.optJSONObject("task")));
                        }
                    } catch (JSONException ignored) {
                    }
                }

                @Override
                public void onBinary(byte[] data) {
                }
            });
            eventsWs = null;
            release(w);
            if (running) retry.failed();
        }
    }

    private void sendAudioText(String text) {
        LocalRoseSocket w = audioWs;
        if (w == null) return;
        try {
            w.sendText(text);
        } catch (IOException e) {
            w.close();
        }
    }

    private void emit(JSONObject m) {
        if (running || "closed".equals(m.optString("type"))) sink.event(m);
    }

    private static JSONObject obj(String... pairs) {
        try {
            JSONObject o = new JSONObject();
            for (int i = 0; i + 1 < pairs.length; i += 2) o.put(pairs[i], pairs[i + 1]);
            return o;
        } catch (JSONException e) {
            throw new IllegalStateException(e);
        }
    }

    private static void join(Thread t) {
        if (t == null) return;
        try {
            t.join(1000);
        } catch (InterruptedException ignored) {
        }
    }
}
