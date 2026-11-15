package dev.pixel.rose.authenticator;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PersistableBundle;
import android.util.AtomicFile;
import android.util.TypedValue;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import dev.pixel.rosesignin.RoseSigninCore;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * The Rose authenticator (rose-signin-v1, D3/D8): the launcher entry lists accounts with their current codes, and an
 * otpauth://totp link (ACTION_VIEW, sent by a generated app's enrollment) adds an account after the user confirms.
 * Plain system UI. The window is FLAG_SECURE (no screenshots or recents thumbnails of codes); a copied code is marked
 * sensitive on the clipboard. Secrets stay sealed by {@link KeystoreSealer}, usable only while the phone is unlocked.
 *
 * <p>The vault file is the single source of truth: every change re-reads it under one process-wide lock (read,
 * modify, write), so two instances of this activity (the launcher's and one started by another app's link) never
 * overwrite each other's accounts. A link never replaces an existing account: the same secret is "already added";
 * a different one is refused until the user removes the old account from the list.
 */
public final class AuthenticatorActivity extends Activity {
    private static final String VAULT_FILE = "vault-v1";
    private static final String EXTRA_IS_SENSITIVE = "android.content.extra.IS_SENSITIVE";
    private static final String STATE_PENDING = "pending_otpauth";
    private static final String STATE_HANDLED = "launch_link_handled";
    private static final String STATE_SENDER = "pending_sender";
    /** How long the confirm button stays disabled after the dialog appears (tapjacking). */
    private static final long CONFIRM_DELAY_MS = 1000;
    private static final Object LOCK = new Object();

    private final Handler tick = new Handler(Looper.getMainLooper());
    private final Vault.Sealer sealer = new KeystoreSealer();
    private Vault vault = new Vault();
    private LinearLayout list;
    private final List<Row> rows = new ArrayList<>();
    /** An enrollment link awaiting the user's answer; kept across recreation. */
    private String pending;
    /** The app that sent the pending link, as Android reports it (may be unknown). */
    private String sender;
    private boolean launchHandled;
    private AlertDialog dialog;
    private boolean resumed;
    /** Confirm buttons under the tapjacking delay; re-armed each time the activity is shown again. */
    private final List<Button> guarded = new ArrayList<>();
    private final Runnable enable = () -> {
        for (Button b : guarded) b.setEnabled(true);
    };

    private static final class Row {
        final Vault.Entry entry;
        final TextView code, left;

        Row(Vault.Entry entry, TextView code, TextView left) {
            this.entry = entry;
            this.code = code;
            this.left = left;
        }
    }

    private final Runnable refresh = new Runnable() {
        @Override public void run() {
            updateCodes();
            long nowMs = System.currentTimeMillis();
            tick.postDelayed(this, 1000 - nowMs % 1000 + 10);
        }
    };

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        int pad = dp(16);
        list.setPadding(pad, pad, pad, pad);
        ScrollView scroll = new ScrollView(this);
        scroll.addView(list);
        setContentView(scroll);
        if (state != null) {
            pending = state.getString(STATE_PENDING);
            sender = state.getString(STATE_SENDER);
            launchHandled = state.getBoolean(STATE_HANDLED);
        }
        if (!launchHandled) {
            launchHandled = true;
            // Reopened from Recents, a task's root intent is the link it was started with, answered long ago.
            Intent launch = getIntent();
            if (launch != null && (launch.getFlags() & Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY) == 0) {
                if (take(launch)) sender = getLaunchedFromPackage();
            }
        }
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (!take(intent)) return;
        // Android names only the app that first launched this activity, so a later link's sender is unknown.
        sender = null;
        if (dialog != null) {
            AlertDialog old = dialog;
            dialog = null;
            old.dismiss();
        }
        // A resumed activity (split screen, freeform) gets new intents without onResume, so ask here as well.
        if (resumed) ask();
    }

    @Override protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        out.putString(STATE_PENDING, pending);
        out.putString(STATE_SENDER, sender);
        out.putBoolean(STATE_HANDLED, launchHandled);
    }

    @Override protected void onResume() {
        super.onResume();
        reload();
        tick.post(refresh);
        resumed = true;
        if (pending != null && dialog == null) ask();
        arm();
    }

    @Override protected void onPause() {
        resumed = false;
        tick.removeCallbacks(refresh);
        // Covered by another activity: the delay starts again when this one is shown.
        tick.removeCallbacks(enable);
        for (Button b : guarded) b.setEnabled(false);
        super.onPause();
    }

    @Override protected void onDestroy() {
        if (dialog != null) dialog.dismiss();
        super.onDestroy();
    }

    /** Remembers an otpauth link from an intent until the user answers it. */
    private boolean take(Intent intent) {
        if (intent == null || !Intent.ACTION_VIEW.equals(intent.getAction())) return false;
        Uri data = intent.getData();
        if (data == null) return false;
        pending = data.toString();
        return true;
    }

    /** Asks about the pending link: add a new account, or explain why it cannot be added. */
    private void ask() {
        final String uri = pending;
        final RoseSigninCore.Enrollment e = RoseSigninCore.parseOtpAuth(uri);
        if (e == null) {
            pending = null;
            toast("This link is not a supported authenticator link.");
            return;
        }
        if (!Vault.displayable(e.issuer) || !Vault.displayable(e.account)) {
            pending = null;
            toast("This link's account name cannot be shown safely, so it was not added.");
            return;
        }
        Vault.Entry existing = vault.find(e.issuer, e.account);
        if (existing != null) {
            pending = null;
            boolean same;
            try {
                same = sameSecret(existing, e);
            } catch (Exception locked) {
                toast("Unlock the phone and open the link again.");
                return;
            }
            new AlertDialog.Builder(this)
                    .setTitle(same ? "Already added" : "This account is already here")
                    .setMessage(e.issuer + "\n" + e.account + (same ? ""
                            : "\n\nA link cannot replace it. To use the new one, remove this account from the list"
                                    + " first, then open the link again."))
                    .setPositiveButton("OK", null)
                    .show();
            return;
        }
        final AlertDialog[] self = new AlertDialog[1];
        dialog = self[0] = new AlertDialog.Builder(this)
                // The sender is in the title, apart from the names the link supplies.
                .setTitle(sender == null ? "Add an account from an unidentified app?" : "Add an account from " + sender + "?")
                .setMessage(e.issuer + "\n" + e.account)
                .setPositiveButton("Add", (d, w) -> {
                    pending = null;
                    try {
                        change(v -> {
                            if (v.find(e.issuer, e.account) != null) throw new AlreadyHere();
                            v.add(sealer, e);
                        });
                    } catch (AlreadyHere meanwhile) {
                        toast("This account is already here.");
                    } catch (Exception ex) {
                        toast("Could not add the account. Unlock the phone and try again.");
                    }
                })
                .setNegativeButton("Cancel", (d, w) -> pending = null)
                .setOnCancelListener(d -> pending = null)
                .create();
        dialog.show();
        final Button add = dialog.getButton(AlertDialog.BUTTON_POSITIVE);
        dialog.setOnDismissListener(d -> {
            if (dialog == self[0]) dialog = null;
            guarded.remove(add);
        });
        guard(add);
    }

    /** Tapjacking: ignore touches while another window covers the button, and enable it only after a delay. */
    private void guard(Button b) {
        if (b == null) return;
        b.setFilterTouchesWhenObscured(true);
        guarded.add(b);
        arm();
    }

    /** Disables every guarded button and enables them again after the delay, if this activity is still shown. */
    private void arm() {
        tick.removeCallbacks(enable);
        for (Button b : guarded) b.setEnabled(false);
        if (resumed && !guarded.isEmpty()) tick.postDelayed(enable, CONFIRM_DELAY_MS);
    }

    private boolean sameSecret(Vault.Entry existing, RoseSigninCore.Enrollment e) throws Exception {
        byte[] stored = existing == null ? null : sealer.open(existing.sealed, Vault.associatedData(e.issuer, e.account));
        byte[] incoming = e.secret();
        try {
            return stored != null && Arrays.equals(stored, incoming);
        } finally {
            if (stored != null) Arrays.fill(stored, (byte) 0);
            Arrays.fill(incoming, (byte) 0);
        }
    }

    /** Another instance added the same account between the question and the answer; nothing is replaced. */
    private static final class AlreadyHere extends Exception {}

    private interface Change {
        void apply(Vault v) throws Exception;
    }

    /** Read, modify, write under the process-wide lock, so no instance writes back a stale vault. */
    private void change(Change c) throws Exception {
        synchronized (LOCK) {
            Vault fresh = load(this);
            c.apply(fresh);
            save(this, fresh);
            vault = fresh;
        }
        buildRows();
    }

    private void reload() {
        synchronized (LOCK) {
            vault = load(this);
        }
        buildRows();
    }

    /** Builds one row per account; called only when the vault changes, so taps and long presses survive ticks. */
    private void buildRows() {
        list.removeAllViews();
        rows.clear();
        if (vault.entries().isEmpty()) {
            list.addView(text("No accounts yet. Open an app's authenticator link to add one.", 16, false));
            return;
        }
        for (final Vault.Entry e : vault.entries()) {
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.VERTICAL);
            row.setPadding(0, dp(12), 0, dp(12));
            row.addView(text(e.issuer, 14, true));
            row.addView(text(e.account, 14, false));
            TextView code = text("", 32, true);
            TextView left = text("", 12, false);
            row.addView(code);
            row.addView(left);
            row.setOnClickListener(v -> {
                Object shown = code.getTag();
                if (shown instanceof String) copy((String) shown);
            });
            row.setOnLongClickListener(v -> {
                confirmRemove(e);
                return true;
            });
            row.setContentDescription(e.issuer + ", " + e.account);
            list.addView(row);
            rows.add(new Row(e, code, left));
        }
        updateCodes();
    }

    /** Updates only the code and countdown text of each row. */
    private void updateCodes() {
        long now = System.currentTimeMillis() / 1000;
        for (Row r : rows) {
            String code;
            try {
                code = vault.code(sealer, r.entry, now);
            } catch (Exception locked) {
                code = null;
            }
            r.code.setTag(code);
            r.code.setTextSize(TypedValue.COMPLEX_UNIT_SP, code == null ? 14 : 32);
            r.code.setText(code == null ? "Unlock the phone to see this code" : spaced(code));
            r.left.setText(code == null ? "" : Vault.secondsLeft(now) + " s");
        }
    }

    private void copy(String code) {
        ClipData clip = ClipData.newPlainText("Code", code);
        PersistableBundle extras = new PersistableBundle();
        extras.putBoolean(EXTRA_IS_SENSITIVE, true);
        clip.getDescription().setExtras(extras);
        getSystemService(ClipboardManager.class).setPrimaryClip(clip);
        toast("Code copied");
    }

    private void confirmRemove(Vault.Entry e) {
        AlertDialog d = new AlertDialog.Builder(this)
                .setTitle("Remove this account?")
                .setMessage(e.issuer + "\n" + e.account + "\n\nYou will need a recovery code to sign in to it again.")
                .setPositiveButton("Remove", (x, w) -> {
                    try {
                        change(v -> v.remove(e.issuer, e.account));
                    } catch (Exception ex) {
                        toast("Could not remove the account.");
                    }
                })
                .setNegativeButton("Cancel", null)
                .create();
        d.show();
        final Button remove = d.getButton(AlertDialog.BUTTON_POSITIVE);
        d.setOnDismissListener(x -> guarded.remove(remove));
        guard(remove);
    }

    /** The vault on disk. An unreadable file is kept aside (never overwritten) and an empty vault is used. */
    private static Vault load(Context c) {
        File file = new File(c.getFilesDir(), VAULT_FILE);
        AtomicFile f = new AtomicFile(file);
        try {
            return Vault.parse(new String(f.readFully(), StandardCharsets.UTF_8));
        } catch (java.io.FileNotFoundException none) {
            return new Vault();
        } catch (IOException | RuntimeException bad) {
            File aside = new File(c.getFilesDir(), VAULT_FILE + ".unreadable-" + System.currentTimeMillis());
            if (!file.renameTo(aside)) throw new IllegalStateException("cannot set an unreadable vault aside", bad);
            return new Vault();
        }
    }

    private static void save(Context c, Vault v) throws IOException {
        AtomicFile f = new AtomicFile(new File(c.getFilesDir(), VAULT_FILE));
        FileOutputStream out = f.startWrite();
        try {
            out.write(v.serialize().getBytes(StandardCharsets.UTF_8));
            f.finishWrite(out);
        } catch (IOException ex) {
            f.failWrite(out);
            throw ex;
        }
    }

    private static String spaced(String code) {
        return code.length() == 6 ? code.substring(0, 3) + " " + code.substring(3) : code;
    }

    private TextView text(String s, int sp, boolean bold) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        if (bold) t.setTypeface(Typeface.DEFAULT_BOLD);
        return t;
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    private void toast(String s) {
        Toast.makeText(this, s, Toast.LENGTH_SHORT).show();
    }
}
