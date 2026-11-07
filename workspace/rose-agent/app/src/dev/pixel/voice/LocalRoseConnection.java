package dev.pixel.voice;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.security.cert.CertificateException;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.SSLSocketFactory;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

/**
 * How the phone reaches Rose on the Mac (rose remote link v1).
 *
 * Paired (QR code from the Mac, LocalRosePairActivity): TLS to the Mac's remote port on each
 * address from the QR (the one that worked last first), trusting only the certificate whose
 * SHA-256 is the pin, then a websocket upgrade signed by this phone's Keystore key. Works over
 * Tailscale; LAN and loopback endpoints are never used. A 401/403 that says the Mac removed
 * this phone (LocalRoseAuth.refusal) reports "unpaired" and slows the loops to one check a
 * minute until the user pairs again or unpairs; a refusal because of the clock reports
 * "offline" with reason "clock", and a replayed nonce or busy Mac is an ordinary retry.
 *
 * Not paired: wait for the owner to pair over Tailscale. No implicit USB connection.
 *
 * Loops retry with {@link Retry}; a default-network change wakes them at once.
 */
final class LocalRoseConnection {
    static final String PREFS = "local-rose";
    private static final String TAG = "LocalRose";
    private static final int CONNECT_TIMEOUT_MS = 2500;
    private static final int HANDSHAKE_TIMEOUT_MS = 8000;
    /** A signed handshake this recent counts as connected even when no socket is open right now. */
    private static final long RECENT_MS = 90000;

    /** The Mac's address after pairing; config never contains a secret (the key is in the Keystore). */
    static final class Paired {
        final String serverName, serverId, pin, deviceId, lastHost;
        final List<String> hosts;
        final int port;

        Paired(String serverName, String serverId, List<String> hosts, int port, String pin, String deviceId, String lastHost) {
            this.serverName = serverName;
            this.serverId = serverId;
            this.hosts = Collections.unmodifiableList(new ArrayList<>(hosts));
            this.port = port;
            this.pin = pin;
            this.deviceId = deviceId;
            this.lastHost = lastHost;
        }

        /** lastHost first, then the rest in QR order. */
        List<String> order() {
            return LocalRoseAuth.tailscaleHosts(hosts, lastHost);
        }
    }

    /** The Mac no longer accepts this phone (or the saved pairing can't be read). */
    static final class Unpaired extends IOException {
        Unpaired() {
            super("pairing not accepted by the backend");
        }
    }

    /** The Mac refused the signature because this phone's clock and the Mac's disagree. */
    static final class ClockOff extends IOException {
        ClockOff() {
            super("the phone's and the backend's clocks disagree");
        }
    }

    // ---- saved pairing ----

    static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences(PREFS, 0);
    }

    /** The "paired" flag is set, whether or not the saved pairing is readable. */
    static boolean pairedFlag(Context c) {
        return prefs(c).getBoolean("paired", false);
    }

    static Paired paired(Context c) {
        SharedPreferences p = prefs(c);
        if (!p.getBoolean("paired", false)) return null;
        String pin = p.getString("pin", "");
        int port = p.getInt("port", 0);
        List<String> hosts = new ArrayList<>();
        try {
            JSONArray a = new JSONArray(p.getString("hosts", "[]"));
            for (int i = 0; i < a.length(); i++) if (LocalRoseAuth.tailscaleHost(a.optString(i))) hosts.add(a.optString(i));
        } catch (JSONException ignored) {
        }
        if (!pin.matches("[0-9a-f]{64}") || port < 1 || port > 65535 || hosts.isEmpty()) return null;
        return new Paired(p.getString("serverName", "your backend"), p.getString("serverId", pin.substring(0, 16)), hosts, port,
                pin, p.getString("deviceId", ""), p.getString("lastHost", null));
    }

    static void savePairing(Context c, LocalRoseAuth.Invite invite, String serverName, String deviceId, String host) {
        prefs(c).edit().putBoolean("paired", true).putString("serverName", serverName)
                .putString("serverId", invite.serverId()).putString("hosts", new JSONArray(invite.hosts).toString())
                .putInt("port", invite.port).putString("pin", invite.pin).putString("deviceId", deviceId)
                .putString("lastHost", host).commit();
        reconnectNow();
    }

    /** Forgets the Mac (the device key stays for pairing again); a new Tailscale pairing is required. */
    static void unpair(Context c) {
        prefs(c).edit().putBoolean("paired", false).remove("serverName").remove("serverId").remove("hosts")
                .remove("port").remove("pin").remove("deviceId").remove("lastHost").commit();
        reconnectNow();
    }

    // ---- connecting ----

    private static final Map<String, SSLSocketFactory> factories = new HashMap<>();
    private static final List<Live> live = new ArrayList<>();
    private static volatile boolean everFailed;
    private static volatile long lastOkAt;
    /** Why the backend last refused this phone: "" (it didn't), "revoked", "clock" or "damaged" (unreadable config). */
    private static volatile String refused = "";
    private static volatile String lastRoute = "";

    private static final class Live {
        final LocalRoseSocket socket;
        final String route;

        Live(LocalRoseSocket socket, String route) {
            this.socket = socket;
            this.route = route;
        }
    }

    /**
     * Opens a websocket to Rose at path ("/ws/audio", "/ws/events", "/ws/phone"). Throws
     * {@link Unpaired} when the Mac refuses this phone, IOException when no address answers.
     * Call {@link #closed} with the socket once its read loop ends.
     */
    static LocalRoseSocket open(Context c, String path) throws IOException {
        Paired p = paired(c);
        if (p == null) {
            refused = pairedFlag(c) ? "damaged" : "";
            throw new Unpaired();
        }
        LocalRoseKey key;
        try {
            key = LocalRoseKey.load();
        } catch (GeneralSecurityException e) {
            throw new IOException("pairing key unavailable", e);
        }
        if (key == null || !key.deviceId().equals(p.deviceId)) throw new IOException("pairing key unavailable");
        IOException last = null;
        for (String host : p.order()) {
            Socket raw = new Socket();
            try {
                Socket s = tls(raw, host, p.port, p.pin);
                Map<String, String> headers;
                try {
                    headers = LocalRoseAuth.headers(p.deviceId, "GET", path, System.currentTimeMillis(),
                            LocalRoseAuth.newNonce(), p.pin, key);
                } catch (GeneralSecurityException e) {
                    raw.close();
                    s.close();
                    throw new IOException("signing failed", e);
                }
                LocalRoseSocket ws = LocalRoseSocket.over(s, raw, host + ":" + p.port, path, headers);
                if (!host.equals(p.lastHost)) prefs(c).edit().putString("lastHost", host).apply();
                return track(ws, LocalRoseAuth.route(host));
            } catch (LocalRoseSocket.HandshakeRefused e) {
                String kind = LocalRoseAuth.refusal(e.status, e.header("X-Rose-Error"), e.header("Date"),
                        System.currentTimeMillis());
                if (kind.equals(LocalRoseAuth.REFUSED_REVOKED)) {
                    Log.w(TAG, "backend refused this phone (" + e.status + "); pair again");
                    refused = kind;
                    throw new Unpaired();
                }
                if (kind.equals(LocalRoseAuth.REFUSED_CLOCK)) {
                    // The phone's clock is the same for every address: no point trying the others.
                    Log.w(TAG, "backend refused this phone's time; check the date and time settings");
                    refused = kind;
                    everFailed = true;
                    throw new ClockOff();
                }
                last = e;
            } catch (IOException e) {
                last = e;
            }
        }
        everFailed = true;
        throw last != null ? last : new IOException("no address for the backend");
    }

    /** TCP on raw (2.5 s connect timeout) then TLS over it that accepts only the pinned certificate. */
    static SSLSocket tls(Socket raw, String host, int port, String pin) throws IOException {
        try {
            raw.setTcpNoDelay(true);
            InetSocketAddress address = null;
            for (java.net.InetAddress candidate : java.net.InetAddress.getAllByName(host)) {
                if (LocalRoseAuth.tailscaleHost(candidate.getHostAddress())) {
                    address = new InetSocketAddress(candidate, port);
                    break;
                }
            }
            if (address == null) throw new IOException("No Tailscale address for the backend. Connect Tailscale on both devices.");
            raw.connect(address, CONNECT_TIMEOUT_MS);
            raw.setSoTimeout(HANDSHAKE_TIMEOUT_MS);
            SSLSocket s = (SSLSocket) pinned(pin).createSocket(raw, host, port, true);
            s.setEnabledProtocols(modern(s.getSupportedProtocols()));
            s.startHandshake();
            return s;
        } catch (IOException e) {
            raw.close();
            throw e;
        }
    }

    /**
     * An SSLSocketFactory that trusts exactly one certificate, the one whose DER SHA-256 is pin.
     * Hostname verification is deliberately not used: the pin is the Mac's identity.
     */
    static synchronized SSLSocketFactory pinned(String pin) throws IOException {
        SSLSocketFactory f = factories.get(pin);
        if (f != null) return f;
        X509TrustManager trust = new X509TrustManager() {
            @Override
            public void checkClientTrusted(X509Certificate[] chain, String auth) throws CertificateException {
                throw new CertificateException("not a server");
            }

            @Override
            public void checkServerTrusted(X509Certificate[] chain, String auth) throws CertificateException {
                if (chain == null || chain.length == 0 || !LocalRoseAuth.pinMatches(chain[0].getEncoded(), pin)) {
                    throw new CertificateException("not the paired backend");
                }
            }

            @Override
            public X509Certificate[] getAcceptedIssuers() {
                return new X509Certificate[0];
            }
        };
        try {
            SSLContext ssl = SSLContext.getInstance("TLS");
            ssl.init(null, new TrustManager[]{trust}, new SecureRandom());
            f = ssl.getSocketFactory();
        } catch (GeneralSecurityException e) {
            throw new IOException(e);
        }
        factories.put(pin, f);
        return f;
    }

    private static String[] modern(String[] supported) {
        List<String> out = new ArrayList<>();
        for (String p : supported) if (p.equals("TLSv1.2") || p.equals("TLSv1.3")) out.add(p);
        return out.toArray(new String[0]);
    }

    private static LocalRoseSocket track(LocalRoseSocket ws, String route) {
        synchronized (live) {
            live.add(new Live(ws, route));
        }
        lastRoute = route;
        everFailed = false;
        refused = "";
        lastOkAt = System.currentTimeMillis();
        return ws;
    }

    /** "Tailscale" for an open socket from {@link #open}. */
    static String routeOf(LocalRoseSocket ws) {
        synchronized (live) {
            for (Live l : live) if (l.socket == ws) return l.route;
        }
        return "";
    }

    /** The socket's read loop ended. */
    static void closed(LocalRoseSocket ws) {
        synchronized (live) {
            for (int i = live.size() - 1; i >= 0; i--) if (live.get(i).socket == ws) live.remove(i);
        }
    }

    /** Pairing changed: drop every open connection and retry now with the new config. */
    static void reconnectNow() {
        refused = "";
        everFailed = false;
        lastOkAt = 0;
        closeAll();
        wake();
    }

    private static void closeAll() {
        lastOkAt = 0;
        List<Live> copy;
        synchronized (live) {
            copy = new ArrayList<>(live);
        }
        for (Live l : copy) l.socket.close();
    }

    /**
     * For the voice page: {"paired","state":connected|connecting|offline|unpaired,"route","serverName","reason"};
     * reason "clock" with offline means the Mac refused this phone's time.
     */
    static String status(Context c) {
        Paired p = paired(c);
        String state, route = "", reason = "";
        boolean up;
        synchronized (live) {
            up = !live.isEmpty();
        }
        String why = refused;
        if (p == null) {
            state = "unpaired";
            reason = pairedFlag(c) ? "pairing_update_required" : "";
        } else if (why.equals(LocalRoseAuth.REFUSED_REVOKED)) {
            state = "unpaired";
            reason = why;
        } else if (up || lastOkAt > 0 && System.currentTimeMillis() - lastOkAt < RECENT_MS && !everFailed) {
            // A recent signed handshake counts: /ws/phone may be closed by a Mac without phone control.
            state = "connected";
            route = lastRoute;
        } else if (why.equals(LocalRoseAuth.REFUSED_CLOCK)) {
            state = "offline";
            reason = why;
        } else {
            state = everFailed ? "offline" : "connecting";
        }
        try {
            return new JSONObject().put("paired", p != null).put("state", state).put("route", route)
                    .put("serverName", p == null ? "" : p.serverName).put("reason", reason).toString();
        } catch (JSONException e) {
            return "{}";
        }
    }

    // ---- waiting and waking ----

    private static final Object WAKE = new Object();
    private static long wakes;

    static void wake() {
        synchronized (WAKE) {
            wakes++;
            WAKE.notifyAll();
        }
    }

    private static long wakeCount() {
        synchronized (WAKE) {
            return wakes;
        }
    }

    /**
     * Retry pacing for one connection loop: 0.5, 1, 2, 4, 8 s (cap) after consecutive failures,
     * reset by a success. A wake (network change, pairing, stop) ends the wait at once.
     */
    static final class Retry {
        /** A connection that ends sooner than this does not reset the ladder (a Mac that accepts then closes). */
        private static final long STEADY_MS = 5000;
        private int failures;
        private long seen = wakeCount();
        private long connectedAt;

        /** Call just before each connection attempt. */
        void attempt() {
            seen = wakeCount();
        }

        void connected() {
            connectedAt = System.currentTimeMillis();
        }

        /** Waits after a failed or ended connection. */
        void failed() {
            if (connectedAt != 0 && System.currentTimeMillis() - connectedAt >= STEADY_MS) failures = 0;
            connectedAt = 0;
            pause(LocalRoseAuth.backoffMs(failures++));
        }

        /** Waits a minute (pairing refused, or the Mac has phone control off); a wake ends it sooner. */
        void parked() {
            connectedAt = 0;
            pause(60000);
        }

        private void pause(long ms) {
            long end = System.currentTimeMillis() + ms;
            synchronized (WAKE) {
                while (wakes == seen) {
                    long left = end - System.currentTimeMillis();
                    if (left <= 0) return;
                    try {
                        WAKE.wait(left);
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                        return;
                    }
                }
                failures = 0;  // woken: the network or pairing changed, so start the ladder again
            }
        }
    }

    // ---- network changes ----

    private static ConnectivityManager.NetworkCallback callback;
    private static Network defaultNetwork;
    private static String defaultShape = "";

    /** Watches the default network (Wi-Fi, mobile, or the Tailscale VPN on top) to reconnect at once. */
    static synchronized void watchNetwork(Context c) {
        if (callback != null) return;
        ConnectivityManager cm = c.getSystemService(ConnectivityManager.class);
        if (cm == null) return;
        callback = new ConnectivityManager.NetworkCallback() {
            @Override
            public void onAvailable(Network network) {
                Network before;
                synchronized (LocalRoseConnection.class) {
                    before = defaultNetwork;
                    defaultNetwork = network;
                }
                // A different default network: sockets on the old one may never error out.
                if (before != null && !before.equals(network)) closeAll();
                wake();
            }

            @Override
            public void onLost(Network network) {
                closeAll();
                wake();
            }

            @Override
            public void onCapabilitiesChanged(Network network, NetworkCapabilities caps) {
                // Signal-strength updates arrive often; only a change in reachability matters.
                String shape = caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED) + "/"
                        + caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) + "/"
                        + caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI);
                boolean changed;
                synchronized (LocalRoseConnection.class) {
                    changed = !shape.equals(defaultShape);
                    defaultShape = shape;
                }
                if (changed) wake();
            }
        };
        try {
            cm.registerDefaultNetworkCallback(callback);
        } catch (RuntimeException e) {
            callback = null;
        }
    }

    static synchronized void stopWatching(Context c) {
        if (callback == null) return;
        ConnectivityManager cm = c.getSystemService(ConnectivityManager.class);
        try {
            if (cm != null) cm.unregisterNetworkCallback(callback);
        } catch (RuntimeException ignored) {
        }
        callback = null;
        defaultNetwork = null;
        defaultShape = "";
    }
}
