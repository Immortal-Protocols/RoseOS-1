package dev.pixel.voice;

import java.io.BufferedInputStream;
import java.io.DataInputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.OutputStream;
import java.net.Socket;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.locks.ReentrantLock;

/**
 * A minimal RFC 6455 websocket client. It runs over a plain socket (ws://, the Mac over adb
 * reverse) or over a socket the caller supplies, such as LocalRoseConnection's pinned TLS
 * socket, with extra upgrade headers (the X-Rose-* request signature).
 * Sends are serialized; {@link #readLoop} blocks on the caller's thread until
 * the connection closes, then {@link #closeCode} says why (-1: lost). While the line is
 * quiet it pings the Mac, and a connection that stays silent for {@link #DEAD_MS} is lost.
 * The read loop never waits behind a stuck send (a dead path with the mic streaming fills the
 * TCP buffer and blocks the writer): its pings skip a busy line, and closing a dead socket
 * closes the TCP socket underneath TLS first, which also frees the blocked writer.
 */
final class LocalRoseSocket {
    interface Listener {
        void onText(String text);
        void onBinary(byte[] data);
    }

    /** The server answered the upgrade with something other than 101; 401/403 mean "not allowed". */
    static final class HandshakeRefused extends IOException {
        final int status;
        private final Map<String, String> headers;

        HandshakeRefused(int status, String line, Map<String, String> headers) {
            super("handshake refused: " + line);
            this.status = status;
            this.headers = headers;
        }

        /** A response header (case-insensitive name), or null. */
        String header(String name) {
            return headers.get(name.toLowerCase(Locale.ROOT));
        }
    }

    private static final SecureRandom RANDOM = new SecureRandom();
    private static final int IDLE_MS = 15000;  // ping after this much quiet
    static final int DEAD_MS = 45000;          // give up after this much silence

    private static final long CONTROL_WAIT_MS = 2000;  // a pong or close waits this long for a busy line

    private final Socket socket, raw;
    private final ReentrantLock sending = new ReentrantLock();
    private final OutputStream out;
    private final DataInputStream in;
    private final byte[] mask = new byte[4];
    private byte[] frame = new byte[4096];
    volatile int closeCode = -1;

    private LocalRoseSocket(Socket socket, Socket raw) throws IOException {
        this.socket = socket;
        this.raw = raw;
        this.out = socket.getOutputStream();
        this.in = new DataInputStream(new BufferedInputStream(socket.getInputStream(), 1 << 16));
    }

    /**
     * The websocket upgrade over layered socket s (TLS) and TCP socket raw; {@link #close} closes raw
     * first so a write blocked inside s cannot hold the close up.
     */
    static LocalRoseSocket over(Socket s, Socket raw, String hostPort, String path, Map<String, String> headers) throws IOException {
        try {
            LocalRoseSocket ws = new LocalRoseSocket(s, raw);
            ws.handshake(hostPort, path, headers);
            s.setSoTimeout(IDLE_MS);
            return ws;
        } catch (IOException e) {
            if (raw != s) try { raw.close(); } catch (IOException ignored) { }
            s.close();
            throw e;
        }
    }

    private void handshake(String hostPort, String path, Map<String, String> headers) throws IOException {
        byte[] nonce = new byte[16];
        RANDOM.nextBytes(nonce);
        String key = Base64.getEncoder().encodeToString(nonce);
        StringBuilder req = new StringBuilder("GET ").append(path).append(" HTTP/1.1\r\nHost: ").append(hostPort)
                .append("\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: ").append(key)
                .append("\r\nSec-WebSocket-Version: 13\r\n");
        for (Map.Entry<String, String> h : headers.entrySet()) {
            if (!h.getKey().matches("[A-Za-z0-9-]+") || !h.getValue().matches("[\\x20-\\x7e]*")) {
                throw new IOException("bad request header");
            }
            req.append(h.getKey()).append(": ").append(h.getValue()).append("\r\n");
        }
        req.append("\r\n");
        out.write(req.toString().getBytes(StandardCharsets.US_ASCII));
        out.flush();

        StringBuilder head = new StringBuilder();
        while (!head.toString().endsWith("\r\n\r\n")) {
            int b = in.read();
            if (b < 0) throw new EOFException("closed during handshake");
            head.append((char) b);
            if (head.length() > 8192) throw new IOException("handshake response too long");
        }
        String[] lines = head.toString().split("\r\n");
        String[] status = lines[0].split(" ", 3);
        int code = status.length >= 2 && status[1].matches("[0-9]{3}") ? Integer.parseInt(status[1]) : -1;
        if (code != 101) {
            Map<String, String> answer = new HashMap<>();
            for (int i = 1; i < lines.length; i++) {
                int c = lines[i].indexOf(':');
                if (c > 0) answer.put(lines[i].substring(0, c).trim().toLowerCase(Locale.ROOT), lines[i].substring(c + 1).trim());
            }
            throw new HandshakeRefused(code, lines[0], answer);
        }
        String expect;
        try {
            byte[] sha = MessageDigest.getInstance("SHA-1").digest(
                    (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").getBytes(StandardCharsets.US_ASCII));
            expect = Base64.getEncoder().encodeToString(sha);
        } catch (Exception e) {
            throw new IOException(e);
        }
        boolean ok = false;
        for (String line : lines) {
            int i = line.indexOf(':');
            if (i > 0 && line.substring(0, i).trim().equalsIgnoreCase("Sec-WebSocket-Accept")
                    && line.substring(i + 1).trim().equals(expect)) ok = true;
        }
        if (!ok) throw new IOException("bad Sec-WebSocket-Accept");
    }

    void sendText(String text) throws IOException {
        byte[] b = text.getBytes(StandardCharsets.UTF_8);
        send(0x1, b, 0, b.length);
    }

    void sendBinary(byte[] data, int off, int len) throws IOException {
        send(0x2, data, off, len);
    }

    private void send(int opcode, byte[] data, int off, int len) throws IOException {
        sending.lock();
        try {
            write(opcode, data, off, len);
        } finally {
            sending.unlock();
        }
    }

    /**
     * A control frame from the read loop: never waits more than waitMs for the line.
     * False when a send was stuck for that long (the frame was not sent).
     */
    private boolean sendControl(int opcode, byte[] data, int len, long waitMs) throws IOException {
        boolean locked;
        try {
            locked = sending.tryLock(waitMs, TimeUnit.MILLISECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        }
        if (!locked) return false;
        try {
            write(opcode, data, 0, len);
            return true;
        } finally {
            sending.unlock();
        }
    }

    /** One masked frame; the caller holds {@link #sending}. */
    private void write(int opcode, byte[] data, int off, int len) throws IOException {
        int need = len + 14;
        if (frame.length < need) frame = new byte[need];
        int n = 0;
        frame[n++] = (byte) (0x80 | opcode);
        if (len < 126) {
            frame[n++] = (byte) (0x80 | len);
        } else if (len < 65536) {
            frame[n++] = (byte) (0x80 | 126);
            frame[n++] = (byte) (len >>> 8);
            frame[n++] = (byte) len;
        } else {
            frame[n++] = (byte) (0x80 | 127);
            for (int i = 7; i >= 0; i--) frame[n++] = (byte) (i >= 4 ? 0 : len >>> (8 * i));
        }
        RANDOM.nextBytes(mask);
        System.arraycopy(mask, 0, frame, n, 4);
        n += 4;
        for (int i = 0; i < len; i++) frame[n + i] = (byte) (data[off + i] ^ mask[i & 3]);
        out.write(frame, 0, n + len);
        out.flush();
    }

    /** Pumps frames to the listener until the connection ends. */
    void readLoop(Listener listener) {
        byte[] partial = null;
        int partialOp = 0;
        try {
            long heard = System.currentTimeMillis();
            while (true) {
                int b0;
                try {
                    b0 = in.readUnsignedByte();
                } catch (SocketTimeoutException quiet) {
                    // Only between frames: a timeout inside a frame would lose our place in the stream.
                    if (System.currentTimeMillis() - heard >= DEAD_MS) throw quiet;
                    // A busy line (a mic frame stuck on a dead path) skips this ping instead of
                    // waiting behind it, so the silence check above still ends the connection.
                    sendControl(0x9, new byte[0], 0, 0);
                    continue;
                }
                heard = System.currentTimeMillis();
                int b1 = in.readUnsignedByte();
                boolean fin = (b0 & 0x80) != 0;
                int op = b0 & 0x0f;
                long len = b1 & 0x7f;
                if (len == 126) len = in.readUnsignedShort();
                else if (len == 127) len = in.readLong();
                if (len > (64 << 20)) throw new IOException("frame too large");
                byte[] m = null;
                if ((b1 & 0x80) != 0) {
                    m = new byte[4];
                    in.readFully(m);
                }
                byte[] payload = new byte[(int) len];
                in.readFully(payload);
                if (m != null) for (int i = 0; i < payload.length; i++) payload[i] ^= m[i & 3];

                if (op == 0x8) {
                    closeCode = payload.length >= 2 ? ((payload[0] & 0xff) << 8) | (payload[1] & 0xff) : 1005;
                    try { sendControl(0x8, payload, Math.min(2, payload.length), CONTROL_WAIT_MS); } catch (IOException ignored) { }
                    return;
                } else if (op == 0x9) {
                    sendControl(0xA, payload, payload.length, CONTROL_WAIT_MS);
                    continue;
                } else if (op == 0xA) {
                    continue;
                }
                if (op != 0) {
                    partialOp = op;
                    partial = payload;
                } else if (partial != null) {
                    byte[] joined = new byte[partial.length + payload.length];
                    System.arraycopy(partial, 0, joined, 0, partial.length);
                    System.arraycopy(payload, 0, joined, partial.length, payload.length);
                    partial = joined;
                }
                if (!fin || partial == null) continue;
                if (partialOp == 0x1) listener.onText(new String(partial, StandardCharsets.UTF_8));
                else if (partialOp == 0x2) listener.onBinary(partial);
                partial = null;
            }
        } catch (IOException e) {
            // lost: closeCode stays -1
        } finally {
            close();
        }
    }

    /** Closes the connection; safe from any thread, and does not wait for a stuck send. */
    void close() {
        if (raw != socket) try { raw.close(); } catch (IOException ignored) { }
        try { socket.close(); } catch (IOException ignored) { }
    }
}
