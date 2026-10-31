package dev.pixel.voice;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.ImageFormat;
import android.graphics.Matrix;
import android.graphics.SurfaceTexture;
import android.hardware.camera2.CameraAccessException;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CaptureRequest;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.media.Image;
import android.media.ImageReader;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.provider.Settings;
import android.util.Size;
import android.view.Gravity;
import android.view.Surface;
import android.view.TextureView;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import com.google.zxing.BarcodeFormat;
import com.google.zxing.BinaryBitmap;
import com.google.zxing.DecodeHintType;
import com.google.zxing.PlanarYUVLuminanceSource;
import com.google.zxing.ReaderException;
import com.google.zxing.common.HybridBinarizer;
import com.google.zxing.qrcode.QRCodeReader;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URL;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.cert.CertificateException;
import java.util.Arrays;
import java.util.Base64;
import java.util.Collections;
import java.util.EnumMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLHandshakeException;
import javax.net.ssl.SSLSocketFactory;

/**
 * Pairs this phone with Rose on a Mac by scanning the QR code the Mac shows (dashboard "Pair
 * phone" or `python -m rose.pair`). Also opened by a roseagent://pair link, so the system Camera
 * app can scan the code too. The user confirms before anything is sent; the window is secure
 * (no screenshots or recents preview) because it shows a live one-time code.
 *
 * Pairing sends the one-time code and this phone's public key (LocalRoseKey) to the Mac over
 * TLS pinned to the certificate hash in the QR, then proves the key works with a signed
 * GET /hello before the pairing is saved (LocalRoseConnection).
 *
 * Because any app or web page can open a roseagent://pair link: the confirm screen asks the
 * user to compare the Mac ID with the one the Mac shows next to its code; a link is refused
 * while this phone is already paired (the in-app scanner can still replace a pairing, and
 * says so); a second link cannot swap the invite behind an open confirm screen or a pairing in
 * progress; touches through an overlay are ignored; and Pair only enables after a moment.
 */
public final class LocalRosePairActivity extends Activity {
    private static final int CAMERA_REQUEST = 81;
    private static final int MAX_RESPONSE = 16384;
    private static final long PAIR_ENABLE_DELAY_MS = 1000;
    private static final String EXTRA_OFFER = "dev.pixel.voice.extra.LOCAL_ROSE_OFFER";
    static final String UNREACHABLE = "Is this phone on the same Wi-Fi or Tailscale as the Mac?";

    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final ExecutorService decoder = Executors.newSingleThreadExecutor();
    private final AtomicBoolean decoding = new AtomicBoolean();
    private final QRCodeReader qr = new QRCodeReader();
    private final Map<DecodeHintType, Object> hints = new EnumMap<>(DecodeHintType.class);

    private TextView title, body, status;
    private FrameLayout frame;
    private TextureView preview;
    private Button primary, secondary;

    private boolean scanning;           // the scanner screen is showing and wants the camera
    private boolean confirming, pairing;  // a confirm screen is open / a pairing is running: links are ignored
    private boolean permissionPending, permissionDenied;  // the camera is asked for once per user action
    private volatile boolean decodeOn;  // frames may still be decoded
    private HandlerThread cameraThread;
    private Handler cameraHandler;
    private CameraDevice camera;
    private boolean opening;
    private int cameraGeneration;  // a camera opened for an older start is closed at once
    private CameraCaptureSession session;
    private ImageReader reader;
    private Size previewSize;
    private int sensorOrientation;

    /**
     * Opens the pairing screen at launch when local mode is on, not paired, and USB Rose is not
     * answering, once per install (later: the menu's Pair with Mac). The offered screen asks for
     * the camera only when the user taps Scan code.
     */
    static void offerOnLaunch(Activity a) {
        if (!LocalRoseLink.enabled(a) || LocalRoseConnection.pairedFlag(a)) return;
        if (LocalRoseConnection.prefs(a).getBoolean("pairOffered", false)) return;
        android.content.Context app = a.getApplicationContext();
        new Thread(() -> {
            if (LocalRoseLink.reachable(app)) return;
            a.runOnUiThread(() -> {
                if (a.isFinishing() || a.isDestroyed()) return;
                LocalRoseConnection.prefs(a).edit().putBoolean("pairOffered", true).apply();
                a.startActivity(new Intent(a, LocalRosePairActivity.class).putExtra(EXTRA_OFFER, true));
            });
        }, "local-rose-pair-offer").start();
    }

    @Override
    public void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE);
        hints.put(DecodeHintType.TRY_HARDER, Boolean.TRUE);
        hints.put(DecodeHintType.POSSIBLE_FORMATS, Collections.singletonList(BarcodeFormat.QR_CODE));
        buildLayout();
        if (!openLink(getIntent())) {
            if (LocalRoseConnection.paired(this) != null) showPaired(null);
            else if (getIntent() != null && getIntent().getBooleanExtra(EXTRA_OFFER, false)) showOffer();
            else showScanner(null);
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        if (confirming || pairing) {
            // Never swap the invite behind an open confirm screen or a pairing in progress.
            if (intent != null && intent.getData() != null && !pairing) {
                status.setText("Another pairing link was ignored. Cancel this one first to use it.");
            }
            return;
        }
        setIntent(intent);
        openLink(intent);
    }

    /** A roseagent://pair link from another app (e.g. the Camera app). True when handled. */
    private boolean openLink(Intent intent) {
        Uri data = intent == null ? null : intent.getData();
        if (data == null || !Intent.ACTION_VIEW.equals(intent.getAction())) return false;
        if (LocalRoseConnection.pairedFlag(this)) {
            // A link can't replace a pairing: only this screen's own scanner can.
            stopCamera();
            showPaired("A pairing link was opened, but this phone is already paired. To pair with a different Mac, tap Scan new code and scan the code on that Mac.");
            return true;
        }
        try {
            LocalRoseAuth.Invite invite = LocalRoseAuth.parseInvite(data.toString());
            stopCamera();
            showConfirm(invite, true);
        } catch (LocalRoseAuth.InvalidInvite e) {
            showScanner(e.getMessage());
        }
        return true;
    }

    // ---- screens ----

    private void buildLayout() {
        float dp = getResources().getDisplayMetrics().density;
        int pad = (int) (24 * dp);
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(pad, pad * 2, pad, pad);
        title = new TextView(this);
        title.setTextSize(27);
        content.addView(title);
        body = new TextView(this);
        body.setTextSize(17);
        body.setPadding(0, (int) (8 * dp), 0, (int) (16 * dp));
        content.addView(body);
        frame = new FrameLayout(this) {
            @Override
            protected void onMeasure(int w, int h) {
                super.onMeasure(w, w);  // a square viewfinder
            }
        };
        preview = new TextureView(this);
        preview.setSurfaceTextureListener(new TextureView.SurfaceTextureListener() {
            @Override
            public void onSurfaceTextureAvailable(SurfaceTexture st, int w, int h) {
                if (scanning) startCamera();
            }

            @Override
            public void onSurfaceTextureSizeChanged(SurfaceTexture st, int w, int h) {
                fitPreview();
            }

            @Override
            public boolean onSurfaceTextureDestroyed(SurfaceTexture st) {
                return true;
            }

            @Override
            public void onSurfaceTextureUpdated(SurfaceTexture st) {
            }
        });
        frame.addView(preview, new FrameLayout.LayoutParams(-1, -1));
        frame.addOnLayoutChangeListener((v, l, t, r, b, ol, ot, or, ob) -> fitPreview());
        content.addView(frame, new LinearLayout.LayoutParams(-1, -2));
        status = new TextView(this);
        status.setTextSize(16);
        status.setPadding(0, (int) (12 * dp), 0, (int) (12 * dp));
        content.addView(status);
        LinearLayout buttons = new LinearLayout(this);
        buttons.setGravity(Gravity.END);
        secondary = new Button(this);
        primary = new Button(this);
        buttons.addView(secondary);
        buttons.addView(primary);
        content.addView(buttons);
        ScrollView scroll = new ScrollView(this);
        scroll.addView(content);
        // Ignore taps that pass through another app's window drawn over this one.
        scroll.setFilterTouchesWhenObscured(true);
        content.setFilterTouchesWhenObscured(true);
        primary.setFilterTouchesWhenObscured(true);
        secondary.setFilterTouchesWhenObscured(true);
        setContentView(scroll);
    }

    /** Opened by offerOnLaunch: explain first, and ask for the camera only when the user taps Scan code. */
    private void showOffer() {
        confirming = false;
        scanning = false;
        frame.setVisibility(View.GONE);
        title.setText("Pair with your Mac");
        body.setText("To use Rose on your Mac over Wi-Fi or Tailscale, scan the code it shows: on the Mac, open Rose and choose Pair phone (or run python -m rose.pair).\n\nYou can do this later from the menu: Pair with Mac.");
        status.setText("");
        button(secondary, "Not now", v -> finish());
        button(primary, "Scan code", v -> showScanner(null));
    }

    /** The scanner; opening it is a user action, so it may ask for the camera (once). */
    private void showScanner(String note) {
        confirming = false;
        permissionDenied = false;
        title.setText("Pair with your Mac");
        body.setText("On the Mac, open Rose and choose Pair phone (or run python -m rose.pair), then point this camera at the code.");
        status.setText(note == null ? "" : note);
        frame.setVisibility(View.VISIBLE);
        button(secondary, "Cancel", v -> finish());
        primary.setVisibility(View.GONE);
        scanning = true;
        askCamera();
    }

    private void showConfirm(LocalRoseAuth.Invite invite, boolean fromLink) {
        scanning = false;
        confirming = true;
        frame.setVisibility(View.GONE);
        title.setText("Pair with " + invite.name + "?");
        StringBuilder text = new StringBuilder("Rose on this Mac will hear you and control this phone.\n\n")
                .append("Check that the Mac shows this Mac ID next to its code:\n").append(groups(invite.serverId()))
                .append("\nPair only if it matches.\n\nAddresses: ").append(join(invite.hosts));
        LocalRoseConnection.Paired current = LocalRoseConnection.paired(this);
        if (current != null && !current.pin.equals(invite.pin)) {
            text.append("\n\nThis replaces this phone's pairing with ").append(current.serverName)
                    .append(" (Mac ID ").append(groups(current.serverId)).append("). That Mac will no longer reach this phone.");
        } else if (current != null) {
            text.append("\n\nThis pairs again with the same Mac.");
        }
        if (fromLink) text.append("\n\nThis code was opened from a link, not this screen's scanner.");
        body.setText(text.toString());
        status.setText("");
        button(secondary, "Cancel", v -> {
            if (fromLink) finish();
            else showScanner(null);
        });
        button(primary, current != null && !current.pin.equals(invite.pin) ? "Replace" : "Pair", v -> pair(invite));
        // A moment to read the screen before Pair works, so a tap meant for something else can't land on it.
        primary.setEnabled(false);
        primary.postDelayed(() -> {
            if (confirming && !pairing) primary.setEnabled(true);
        }, PAIR_ENABLE_DELAY_MS);
    }

    private void showPaired(String note) {
        scanning = false;
        confirming = false;
        frame.setVisibility(View.GONE);
        LocalRoseConnection.Paired p = LocalRoseConnection.paired(this);
        if (p == null) {
            showScanner(note);
            return;
        }
        title.setText("Paired with " + p.serverName);
        String state = "";
        try {
            JSONObject s = new JSONObject(LocalRoseConnection.status(this));
            switch (s.optString("state")) {
                case "connected": state = "Connected · " + s.optString("route"); break;
                case "unpaired": state = "The Mac no longer accepts this phone. Scan a new code to pair again."; break;
                case "offline":
                    state = "clock".equals(s.optString("reason"))
                            ? "The Mac refused this phone's time. Turn on automatic date and time in Settings; it reconnects by itself."
                            : "Mac offline. " + UNREACHABLE;
                    break;
                default: state = "Connecting…";
            }
        } catch (JSONException ignored) {
        }
        body.setText("Addresses: " + join(p.hosts) + "\nMac ID: " + groups(p.serverId)
                + "\n\nTo remove this phone on the Mac as well, use Pair phone › Remove there.");
        status.setText(note != null ? note : state);
        button(secondary, "Unpair", v -> new AlertDialog.Builder(this)
                .setTitle("Unpair from " + p.serverName + "?")
                .setMessage("Rose on the Mac won't reach this phone until you pair again.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Unpair", (d, w) -> {
                    LocalRoseConnection.unpair(this);
                    showScanner("Unpaired.");
                }).show());
        button(primary, "Scan new code", v -> showScanner(null));
    }

    private static void button(Button b, String label, View.OnClickListener click) {
        b.setText(label);
        b.setEnabled(true);
        b.setVisibility(View.VISIBLE);
        b.setOnClickListener(click);
    }

    // ---- pairing ----

    /** A message for the user; never contains the code, key or a server error body. */
    private static final class Failure extends Exception {
        Failure(String message) {
            super(message);
        }
    }

    private void pair(LocalRoseAuth.Invite invite) {
        if (pairing || !confirming) return;
        pairing = true;
        primary.setEnabled(false);
        secondary.setEnabled(false);
        status.setText("Pairing securely…");
        io.execute(() -> {
            String message;
            boolean ok = false;
            try {
                message = "Paired with " + pairNow(invite);
                ok = true;
            } catch (Failure e) {
                message = e.getMessage();
            } catch (GeneralSecurityException e) {
                message = e.getMessage() != null && e.getMessage().startsWith("This phone")
                        ? e.getMessage() : "This phone's secure key storage isn't working, so it can't pair.";
            } catch (Exception e) {
                message = "Pairing failed. Make a new code on the Mac and try again.";
            }
            final String shown = message;
            final boolean done = ok;
            runOnUiThread(() -> {
                pairing = false;
                if (isFinishing() || isDestroyed()) return;
                status.setText(shown);
                if (done) {
                    confirming = false;
                    pairing = true;  // finishing: still ignore links
                    title.setText(shown);
                    primary.setVisibility(View.GONE);
                    secondary.setVisibility(View.GONE);
                    status.postDelayed(this::finish, 1500);
                } else {
                    primary.setEnabled(true);
                    secondary.setEnabled(true);
                }
            });
        });
    }

    /** POST /pair to each address in order; returns the Mac's name once saved. */
    private String pairNow(LocalRoseAuth.Invite invite) throws Exception {
        LocalRoseKey key = LocalRoseKey.loadOrCreate(this);
        byte[] request = new JSONObject().put("code", invite.code)
                .put("publicKey", Base64.getEncoder().encodeToString(key.publicKey()))
                .put("name", clip(deviceName())).put("model", clip(Build.MODEL))
                .toString().getBytes(StandardCharsets.UTF_8);
        SSLSocketFactory tls = LocalRoseConnection.pinned(invite.pin);
        boolean wrongIdentity = false;
        for (String host : invite.hosts) {
            HttpsURLConnection c = open(tls, host, invite.port, "/pair");
            int code;
            String text;
            try {
                c.setRequestMethod("POST");
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                c.setFixedLengthStreamingMode(request.length);
                try (OutputStream out = c.getOutputStream()) {
                    out.write(request);
                }
                code = c.getResponseCode();
                text = read(code < 400 ? c.getInputStream() : c.getErrorStream());
            } catch (SSLHandshakeException e) {
                if (causedBy(e, CertificateException.class)) wrongIdentity = true;
                continue;
            } catch (IOException e) {
                continue;  // not reachable at this address; try the next
            } finally {
                c.disconnect();
            }
            if (code == 403) throw new Failure("That code expired or was used. Make a new one on the Mac.");
            if (code == 400) throw new Failure("The Mac couldn't read this phone's request. Update Rose Agent and Rose on the Mac.");
            if (code != 200) throw new Failure("The Mac couldn't pair right now (error " + code + "). Make a new code and try again.");
            JSONObject r;
            try {
                r = new JSONObject(text);
            } catch (JSONException e) {
                throw new Failure("The Mac's answer wasn't understood. Update Rose Agent and Rose on the Mac.");
            }
            if (!key.deviceId().equals(r.optString("deviceId")) || !invite.serverId().equals(r.optString("serverId"))) {
                throw new Failure("The Mac's answer didn't match this phone. Make a new code and try again.");
            }
            String name = r.optString("serverName", invite.name);
            if (name.trim().isEmpty() || name.length() > 128) name = invite.name;
            verify(tls, host, invite, key);
            LocalRoseConnection.savePairing(this, invite, name, key.deviceId(), host);
            return name;
        }
        if (wrongIdentity) throw new Failure("The computer that answered isn't the Mac in this code. Make a new code on the Mac and scan it again.");
        throw new Failure("Couldn't reach " + invite.name + ". " + UNREACHABLE);
    }

    /** A signed GET /hello: proves the Mac accepts this phone's key before the pairing is saved. */
    private void verify(SSLSocketFactory tls, String host, LocalRoseAuth.Invite invite, LocalRoseKey key) throws Exception {
        HttpsURLConnection c = open(tls, host, invite.port, "/hello");
        try {
            Map<String, String> headers = LocalRoseAuth.headers(key.deviceId(), "GET", "/hello",
                    System.currentTimeMillis(), LocalRoseAuth.newNonce(), invite.pin, key);
            for (Map.Entry<String, String> h : headers.entrySet()) c.setRequestProperty(h.getKey(), h.getValue());
            int code = c.getResponseCode();
            if (code == 401 || code == 403) {
                throw new Failure("The Mac didn't accept this phone's signature. Check that the phone's date and time are set automatically, then make a new code on the Mac.");
            }
        } catch (IOException e) {
            // Paired a moment ago at this address; the link retries by itself.
        } finally {
            c.disconnect();
        }
    }

    private static HttpsURLConnection open(SSLSocketFactory tls, String host, int port, String path) throws IOException {
        HttpsURLConnection c = (HttpsURLConnection) new URL("https", host, port, path).openConnection();
        c.setSSLSocketFactory(tls);
        // The trust manager accepts only the pinned certificate, so the pin (not the name) is the identity.
        c.setHostnameVerifier((name, ssl) -> true);
        c.setConnectTimeout(2500);
        c.setReadTimeout(10000);
        c.setUseCaches(false);
        return c;
    }

    private static String read(InputStream in) throws IOException {
        if (in == null) return "";
        try (InputStream s = in) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int n;
            while ((n = s.read(buf)) > 0) {
                out.write(buf, 0, n);
                if (out.size() > MAX_RESPONSE) throw new IOException("response too large");
            }
            return new String(out.toByteArray(), StandardCharsets.UTF_8);
        }
    }

    private String deviceName() {
        String name = Settings.Global.getString(getContentResolver(), Settings.Global.DEVICE_NAME);
        return name == null || name.trim().isEmpty() ? Build.MODEL : name.trim();
    }

    private static String clip(String s) {
        String t = s == null ? "" : s.replaceAll("\\p{Cntrl}", "");
        return t.length() > 64 ? t.substring(0, 64) : t;
    }

    private static boolean causedBy(Throwable t, Class<? extends Throwable> type) {
        for (int i = 0; t != null && i < 8; i++, t = t.getCause()) if (type.isInstance(t)) return true;
        return false;
    }

    private static String join(java.util.List<String> hosts) {
        StringBuilder s = new StringBuilder();
        for (String h : hosts) s.append(s.length() == 0 ? "" : ", ").append(h);
        return s.toString();
    }

    private static String groups(String id) {
        StringBuilder s = new StringBuilder();
        for (int i = 0; i < id.length(); i += 4) s.append(i == 0 ? "" : " ").append(id, i, Math.min(id.length(), i + 4));
        return s.toString();
    }

    // ---- camera ----

    @Override
    protected void onResume() {
        super.onResume();
        if (scanning) startCamera();
    }

    @Override
    protected void onPause() {
        stopCamera();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        stopCamera();
        io.shutdownNow();
        decoder.shutdownNow();
        super.onDestroy();
    }

    @Override
    public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(request, permissions, results);
        if (request != CAMERA_REQUEST) return;
        permissionPending = false;
        if (results.length == 0) return;  // the request was cut short; the screen keeps its state
        if (results[0] == PackageManager.PERMISSION_GRANTED) {
            if (scanning) startCamera();
            return;
        }
        permissionDenied = true;
        if (scanning) cameraDenied();
    }

    private boolean hasCamera() {
        return checkSelfPermission("android.permission.CAMERA") == PackageManager.PERMISSION_GRANTED;
    }

    /** For a user action: starts the camera, or asks for it once (never again from onResume after a denial). */
    private void askCamera() {
        if (hasCamera()) {
            startCamera();
        } else if (permissionDenied) {
            cameraDenied();
        } else if (!permissionPending) {
            permissionPending = true;
            requestPermissions(new String[]{"android.permission.CAMERA"}, CAMERA_REQUEST);
        }
    }

    /** No camera: say so, and offer a button that asks again or, once Android stops asking, opens settings. */
    private void cameraDenied() {
        status.setText("Rose Agent needs the camera to scan the code. You can also scan it with the Camera app.");
        boolean canAsk = shouldShowRequestPermissionRationale("android.permission.CAMERA");
        button(primary, canAsk ? "Allow camera" : "Open settings", v -> {
            if (hasCamera()) {
                primary.setVisibility(View.GONE);
                startCamera();
            } else if (shouldShowRequestPermissionRationale("android.permission.CAMERA")) {
                permissionDenied = false;
                askCamera();
            } else {
                startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + getPackageName())));
            }
        });
    }

    /** Opens the camera when permission is already held; never asks (see askCamera). */
    private void startCamera() {
        if (camera != null || opening || !scanning) return;
        if (!hasCamera()) return;
        if (primary.getVisibility() == View.VISIBLE && !confirming) primary.setVisibility(View.GONE);
        if (!preview.isAvailable()) return;  // onSurfaceTextureAvailable starts it
        CameraManager cm = getSystemService(CameraManager.class);
        try {
            String id = null;
            CameraCharacteristics chars = null;
            for (String c : cm.getCameraIdList()) {
                CameraCharacteristics cc = cm.getCameraCharacteristics(c);
                Integer facing = cc.get(CameraCharacteristics.LENS_FACING);
                if (facing != null && facing == CameraCharacteristics.LENS_FACING_BACK) {
                    id = c;
                    chars = cc;
                    break;
                }
            }
            if (id == null) {
                status.setText("No back camera found. Scan the code with the Camera app instead.");
                return;
            }
            StreamConfigurationMap map = chars.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
            Integer orientation = chars.get(CameraCharacteristics.SENSOR_ORIENTATION);
            sensorOrientation = orientation == null ? 90 : orientation;
            Size yuv = closest(map.getOutputSizes(ImageFormat.YUV_420_888), 1280, 720);
            previewSize = closest(map.getOutputSizes(SurfaceTexture.class), yuv.getWidth(), yuv.getHeight());
            cameraThread = new HandlerThread("local-rose-camera");
            cameraThread.start();
            cameraHandler = new Handler(cameraThread.getLooper());
            reader = ImageReader.newInstance(yuv.getWidth(), yuv.getHeight(), ImageFormat.YUV_420_888, 2);
            reader.setOnImageAvailableListener(this::onFrame, cameraHandler);
            decodeOn = true;
            fitPreview();
            final int generation = ++cameraGeneration;
            opening = true;
            cm.openCamera(id, new CameraDevice.StateCallback() {
                @Override
                public void onOpened(CameraDevice device) {
                    runOnUiThread(() -> {
                        if (generation != cameraGeneration || !scanning || cameraHandler == null) {
                            device.close();
                            return;
                        }
                        opening = false;
                        camera = device;
                        startSession(device);
                    });
                }

                @Override
                public void onDisconnected(CameraDevice device) {
                    device.close();
                    runOnUiThread(() -> {
                        if (generation == cameraGeneration) opening = false;
                        if (camera == device) camera = null;
                    });
                }

                @Override
                public void onError(CameraDevice device, int error) {
                    device.close();
                    runOnUiThread(() -> {
                        if (generation == cameraGeneration) opening = false;
                        if (camera == device) camera = null;
                        status.setText("The camera isn't available right now. You can also scan the code with the Camera app.");
                    });
                }
            }, cameraHandler);
        } catch (CameraAccessException | SecurityException | IllegalArgumentException e) {
            stopCamera();
            status.setText("The camera isn't available right now. You can also scan the code with the Camera app.");
        }
    }

    @SuppressWarnings("deprecation")  // createCaptureSession(List, ...) is fine on every supported release
    private void startSession(CameraDevice device) {
        SurfaceTexture st = preview.getSurfaceTexture();
        if (st == null || reader == null) return;
        st.setDefaultBufferSize(previewSize.getWidth(), previewSize.getHeight());
        Surface view = new Surface(st);
        Surface frames = reader.getSurface();
        try {
            device.createCaptureSession(Arrays.asList(view, frames), new CameraCaptureSession.StateCallback() {
                @Override
                public void onConfigured(CameraCaptureSession s) {
                    if (camera != device) {
                        s.close();
                        return;
                    }
                    session = s;
                    try {
                        CaptureRequest.Builder b = device.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW);
                        b.addTarget(view);
                        b.addTarget(frames);
                        b.set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE);
                        s.setRepeatingRequest(b.build(), null, cameraHandler);
                    } catch (CameraAccessException | IllegalStateException ignored) {
                    }
                }

                @Override
                public void onConfigureFailed(CameraCaptureSession s) {
                    runOnUiThread(() -> status.setText("The camera couldn't start. You can also scan the code with the Camera app."));
                }
            }, cameraHandler);
        } catch (CameraAccessException | IllegalStateException e) {
            status.setText("The camera couldn't start. You can also scan the code with the Camera app.");
        }
    }

    private void stopCamera() {
        decodeOn = false;
        opening = false;
        cameraGeneration++;
        if (session != null) {
            try {
                session.close();
            } catch (RuntimeException ignored) {
            }
            session = null;
        }
        if (camera != null) {
            camera.close();
            camera = null;
        }
        // Close the reader on the camera thread, after any onFrame copy in progress: closing it
        // here would free the image buffers under that copy. quitSafely runs the close first.
        final ImageReader r = reader;
        reader = null;
        if (cameraThread != null) {
            if (r != null && !cameraHandler.post(r::close)) r.close();
            cameraThread.quitSafely();
            cameraThread = null;
            cameraHandler = null;
        } else if (r != null) {
            r.close();
        }
    }

    /** Camera thread: hands the newest frame's Y plane to the decoder, one frame at a time. */
    private void onFrame(ImageReader r) {
        Image img;
        try {
            img = r.acquireLatestImage();
        } catch (IllegalStateException e) {
            return;
        }
        if (img == null) return;
        try {
            if (!decodeOn || !decoding.compareAndSet(false, true)) return;
            Image.Plane y = img.getPlanes()[0];
            int width = img.getWidth(), height = img.getHeight();
            int rowStride = y.getRowStride(), pixelStride = y.getPixelStride();
            ByteBuffer buf = y.getBuffer();
            final byte[] luma;
            final int dataWidth;
            if (pixelStride == 1) {
                // Keep the row padding and tell ZXing the real row length.
                dataWidth = rowStride;
                luma = new byte[rowStride * height];
                buf.get(luma, 0, Math.min(buf.remaining(), luma.length));
            } else {
                dataWidth = width;
                luma = new byte[width * height];
                for (int row = 0; row < height; row++) {
                    for (int col = 0; col < width; col++) luma[row * width + col] = buf.get(row * rowStride + col * pixelStride);
                }
            }
            try {
                decoder.execute(() -> decode(luma, dataWidth, width, height));
            } catch (RuntimeException e) {
                decoding.set(false);
            }
        } catch (IllegalStateException e) {
            decoding.set(false);  // the image was closed under us (camera stopping): skip this frame
        } finally {
            try {
                img.close();
            } catch (RuntimeException ignored) {
            }
        }
    }

    private void decode(byte[] luma, int dataWidth, int width, int height) {
        try {
            if (!decodeOn) return;
            PlanarYUVLuminanceSource source = new PlanarYUVLuminanceSource(luma, dataWidth, height, 0, 0, width, height, false);
            String text = qr.decode(new BinaryBitmap(new HybridBinarizer(source)), hints).getText();
            runOnUiThread(() -> scanned(text));
        } catch (ReaderException | RuntimeException ignored) {
            // no code in this frame
        } finally {
            qr.reset();
            decoding.set(false);
        }
    }

    private void scanned(String text) {
        if (!scanning) return;
        try {
            LocalRoseAuth.Invite invite = LocalRoseAuth.parseInvite(text);
            stopCamera();
            showConfirm(invite, false);
        } catch (LocalRoseAuth.InvalidInvite e) {
            status.setText(e.getMessage() + " Point the camera at the code on your Mac.");
        }
    }

    /** Centre-crops the camera image into the square viewfinder instead of stretching it. */
    private void fitPreview() {
        if (previewSize == null || preview.getWidth() == 0 || preview.getHeight() == 0) return;
        @SuppressWarnings("deprecation") int rotation = getWindowManager().getDefaultDisplay().getRotation();
        int display = rotation * 90;
        boolean swap = ((sensorOrientation - display + 360) % 180) != 0;
        float bw = swap ? previewSize.getHeight() : previewSize.getWidth();
        float bh = swap ? previewSize.getWidth() : previewSize.getHeight();
        float vw = preview.getWidth(), vh = preview.getHeight();
        float sx = vw / bw, sy = vh / bh, s = Math.max(sx, sy);
        Matrix m = new Matrix();
        m.setScale(s / sx, s / sy, vw / 2f, vh / 2f);
        preview.setTransform(m);
    }

    private static Size closest(Size[] sizes, int w, int h) {
        Size best = sizes[0];
        long bestScore = Long.MAX_VALUE;
        for (Size s : sizes) {
            long score = Math.abs((long) s.getWidth() * s.getHeight() - (long) w * h)
                    + 4000L * Math.abs(s.getWidth() * h - s.getHeight() * w) / Math.max(1, h);
            if (score < bestScore) {
                bestScore = score;
                best = s;
            }
        }
        return best;
    }
}
