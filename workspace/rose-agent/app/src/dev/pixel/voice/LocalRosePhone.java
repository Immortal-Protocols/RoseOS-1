package dev.pixel.voice;

import android.app.SearchManager;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.net.Uri;
import android.os.SystemClock;
import android.provider.AlarmClock;
import android.provider.MediaStore;
import android.view.KeyEvent;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Local Rose's hands: phone commands from the Mac on /ws/phone (see LocalRoseLink). This link
 * runs whenever local mode is on, so it is also how the phone stays connected to the Mac in
 * the background (over Tailscale once paired; LocalRoseConnection).
 *
 * Screen commands (inspect_ui, tap_label, tap, set_text, open_app, list_apps, home, back...)
 * run through the same on-device path as the backend's, VoiceService.localPhoneCommand, so
 * they keep its receipts, stale-screen, password and lock-screen guards. A few direct Android
 * intents (alarms, timers, directions, places, music) skip tapping; after one, the screen is
 * read so the agent sees what it did. Nothing here sends a message or confirms a purchase.
 *
 * Wire: Mac -> phone {"id": uuid, "action": ..., ...args}; phone -> Mac
 * {"type": "result", "id": uuid, "result": {...}}; on connect the phone says hello.
 */
final class LocalRosePhone {
    private static final long RUNNING_WAIT_MS = 20000;
    /** The Mac closes /ws/phone with this when it runs without phone control (--phone fake). */
    private static final int PHONE_OFF = 4003;
    private static LocalRosePhone current;

    private final VoiceService service;
    private final ExecutorService work = Executors.newSingleThreadExecutor();
    private volatile boolean running = true;
    private volatile LocalRoseSocket ws;
    private final Thread thread;

    static synchronized void start(VoiceService service) {
        if (current != null) return;
        LocalRoseConnection.watchNetwork(service);
        current = new LocalRosePhone(service);
    }

    static synchronized void stop() {
        if (current == null) return;
        LocalRoseSocket w;
        synchronized (current) {
            current.running = false;
            w = current.ws;
        }
        if (w != null) w.close();
        current.work.shutdownNow();
        LocalRoseConnection.stopWatching(current.service);
        LocalRoseConnection.wake();  // ends a retry wait
        current = null;
    }

    private LocalRosePhone(VoiceService service) {
        this.service = service;
        thread = new Thread(this::loop, "local-rose-phone");
        thread.start();
    }

    private void loop() {
        LocalRoseConnection.Retry retry = new LocalRoseConnection.Retry();
        while (running) {
            LocalRoseSocket w = null;
            retry.attempt();
            try {
                w = LocalRoseConnection.open(service, "/ws/phone");
                synchronized (this) {
                    // stop() while connecting: a late socket must not outlive this link.
                    if (running) ws = w;
                }
                if (ws != w) {
                    w.close();
                    LocalRoseConnection.closed(w);
                    break;
                }
                w.sendText(new JSONObject().put("type", "hello").put("client", "rose-agent")
                        .put("model", android.os.Build.MODEL).toString());
            } catch (LocalRoseConnection.Unpaired e) {
                retry.parked();  // until the user pairs again or unpairs
                continue;
            } catch (IOException | JSONException e) {
                if (w != null) {
                    w.close();
                    LocalRoseConnection.closed(w);
                }
                ws = null;
                retry.failed();
                continue;
            }
            retry.connected();
            final LocalRoseSocket socket = w;
            w.readLoop(new LocalRoseSocket.Listener() {
                @Override
                public void onText(String text) {
                    try {
                        JSONObject c = new JSONObject(text);
                        work.execute(() -> handle(socket, c));
                    } catch (Exception ignored) {
                    }
                }

                @Override
                public void onBinary(byte[] data) {
                }
            });
            ws = null;
            LocalRoseConnection.closed(w);
            if (!running) break;
            // Rose without phone control: the link works, so check back now and then instead of looping.
            if (w.closeCode == PHONE_OFF) retry.parked();
            else retry.failed();
        }
    }

    private void handle(LocalRoseSocket w, JSONObject c) {
        String id = c.optString("id");
        JSONObject result;
        try {
            result = direct(c);
            if (result == null) result = command(c);
        } catch (Exception e) {
            result = new JSONObject();
            try {
                result.put("status", "error").put("message", e.getClass().getSimpleName() + ": " + e.getMessage());
            } catch (JSONException ignored) {
            }
        }
        try {
            w.sendText(new JSONObject().put("type", "result").put("id", id).put("result", result).toString());
        } catch (IOException | JSONException ignored) {
            // the Mac reconnects and times the command out
        }
    }

    /** One screen command through VoiceService, waiting out a "running" receipt. */
    private JSONObject command(JSONObject c) throws JSONException {
        if (!c.has("expires")) c.put("expires", System.currentTimeMillis() + RUNNING_WAIT_MS);
        String raw = c.toString();
        long deadline = SystemClock.uptimeMillis() + RUNNING_WAIT_MS;
        JSONObject r = new JSONObject(service.localPhoneCommand(raw));
        while ("running".equals(r.optString("status")) && SystemClock.uptimeMillis() < deadline && running) {
            SystemClock.sleep(150);
            r = new JSONObject(service.localPhoneCommand(raw));  // the same id returns the saved result
        }
        return r;
    }

    private JSONObject observe() throws JSONException {
        return command(new JSONObject().put("id", UUID.randomUUID().toString()).put("action", "inspect_ui"));
    }

    /** Direct intents; null when the action is a screen command. */
    private JSONObject direct(JSONObject c) throws JSONException {
        String action = c.optString("action");
        Intent i;
        switch (action) {
            case "set_alarm":
                i = new Intent(AlarmClock.ACTION_SET_ALARM)
                        .putExtra(AlarmClock.EXTRA_HOUR, c.getInt("hour"))
                        .putExtra(AlarmClock.EXTRA_MINUTES, c.getInt("minute"))
                        .putExtra(AlarmClock.EXTRA_SKIP_UI, true);
                if (!c.optString("label").isEmpty()) i.putExtra(AlarmClock.EXTRA_MESSAGE, c.optString("label"));
                break;
            case "set_timer":
                i = new Intent(AlarmClock.ACTION_SET_TIMER)
                        .putExtra(AlarmClock.EXTRA_LENGTH, c.getInt("seconds"))
                        .putExtra(AlarmClock.EXTRA_SKIP_UI, true);
                if (!c.optString("label").isEmpty()) i.putExtra(AlarmClock.EXTRA_MESSAGE, c.optString("label"));
                break;
            case "directions": {
                String mode = c.optString("mode", "driving");
                if (!mode.matches("walking|driving|transit|bicycling")) mode = "driving";
                i = new Intent(Intent.ACTION_VIEW, Uri.parse("https://www.google.com/maps/dir/?api=1&destination="
                        + Uri.encode(c.getString("destination")) + "&travelmode=" + mode + "&dir_action=navigate"))
                        .setPackage("com.google.android.apps.maps");
                break;
            }
            case "search_places":
                i = new Intent(Intent.ACTION_VIEW, Uri.parse("geo:0,0?q=" + Uri.encode(c.getString("query"))))
                        .setPackage("com.google.android.apps.maps");
                break;
            case "play_music":
                i = new Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH)
                        .putExtra(MediaStore.EXTRA_MEDIA_FOCUS, "vnd.android.cursor.item/*")
                        .putExtra(SearchManager.QUERY, c.getString("query"));
                if (!c.optString("package").isEmpty()) i.setPackage(c.optString("package"));
                break;
            case "media_key": {
                int key;
                switch (c.optString("key")) {
                    case "pause": key = KeyEvent.KEYCODE_MEDIA_PAUSE; break;
                    case "play": key = KeyEvent.KEYCODE_MEDIA_PLAY; break;
                    case "next": key = KeyEvent.KEYCODE_MEDIA_NEXT; break;
                    case "previous": key = KeyEvent.KEYCODE_MEDIA_PREVIOUS; break;
                    default: return new JSONObject().put("status", "error").put("message", "unknown media key");
                }
                android.media.AudioManager am = service.getSystemService(android.media.AudioManager.class);
                am.dispatchMediaKeyEvent(new KeyEvent(KeyEvent.ACTION_DOWN, key));
                am.dispatchMediaKeyEvent(new KeyEvent(KeyEvent.ACTION_UP, key));
                return new JSONObject().put("status", "action_accepted").put("key", c.optString("key"));
            }
            default:
                return null;
        }
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            service.startActivity(i);
        } catch (ActivityNotFoundException e) {
            return new JSONObject().put("status", "not_found").put("message", "No app on the phone handles " + action);
        }
        SystemClock.sleep(action.equals("set_alarm") || action.equals("set_timer") ? 900 : 1500);
        return new JSONObject().put("status", "started").put("action", action).put("observation", observe());
    }
}
