package dev.pixel.models;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Rose Models text chat: transcript, input and Send, status, Stop, Continue and the last app
 * action. Conversation and agent state live in Core's local-agent-v1 agent on this phone
 * (NativeAgent); inference goes only to the public Rose Models service (ModelsService): Muse
 * Glimmer, or Qwen when Settings selects it. Its access token comes from service-auth-v1
 * (DeviceAuth: an attested Keystore key), stays in memory and is renewed automatically; nothing is typed and no secret ships in the APK.
 */
public final class MainActivity extends Activity implements TurnView {
    private static final String PREFS = "rose-models";
    private static final String KEY_ALLOWLIST = "allowlist";
    /** The selected model's exact served id; anything not in ModelsService.Model means the default. */
    private static final String KEY_MODEL = "model_id";
    /** Settings of builds before 0.2: removed on start; chat and app data are untouched. */
    private static final String[] STALE_KEYS = {"endpoint", "model"};

    private final Handler main = new Handler(Looper.getMainLooper());
    private LocalAgentHost host;
    private TextView transcript;
    private TextView status;
    private TextView actionCard;
    private ScrollView scroller;
    private EditText input;
    private Button send;
    private Button stop;
    private Button resume;
    private AppAllowlist allowlist = AppAllowlist.empty();
    private String setupProblem;
    private DeviceAuth auth;
    private ModelsService.Model model = ModelsService.DEFAULT;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        SharedPreferences prefs = prefs();
        SharedPreferences.Editor stale = prefs.edit();
        for (String key : STALE_KEYS) {
            stale.remove(key);
        }
        stale.apply();
        allowlist = AppAllowlist.parse(prefs.getString(KEY_ALLOWLIST, ""));
        model = ModelsService.Model.fromId(prefs.getString(KEY_MODEL, null));
        buildUi();
        auth = new DeviceAuth(new KeystoreDeviceKey(), store(prefs));
        String inviteProblem = takeInvite(getIntent());
        NativeAgent agent;
        try {
            agent = new NativeAgent(AgentConfigJson.build(model, allowlist));
        } catch (IllegalArgumentException e) {
            setupProblem = "the agent rejected its configuration: " + e.getMessage();
            setStatus(setupProblem);
            setControls(false, false, false);
            return;
        }
        host = new LocalAgentHost(agent, new HttpsInferenceClient(ModelsService.CHAT_URL, auth),
                new AllowlistLauncher(allowlist, new AndroidAppLauncher(this)), this, main::post,
                (millis, task) -> main.postDelayed(task, millis));
        connect();
        if (inviteProblem != null) {
            setStatus(inviteProblem);
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        String problem = takeInvite(intent);
        if (problem != null) {
            setStatus(problem);
        } else if (host != null) {
            connect();
        }
    }

    /**
     * Reads a scanned invite link once: the exact form only (InviteLink), handed to DeviceAuth in memory and
     * removed from the intent so a recreate never reads it again. Returns a problem to show, or null.
     */
    private String takeInvite(Intent intent) {
        if (intent == null || !Intent.ACTION_VIEW.equals(intent.getAction()) || intent.getData() == null) {
            return null;
        }
        String code = InviteLink.parse(intent.getDataString());
        intent.setData(null);
        setIntent(new Intent(Intent.ACTION_MAIN));
        if (code == null) {
            return "That is not a Rose Models sign-in code.";
        }
        if (!auth.offerInvite(code)) {
            return auth.state() == DeviceAuth.State.ENROLLED ? "This phone is already signed in."
                    : "Choose Settings → Reset sign-in before using a new code.";
        }
        return null;
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (host != null) {
            host.onForeground();
        }
    }

    @Override
    protected void onPause() {
        if (host != null) {
            host.onBackground();
        }
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        if (host != null) {
            host.onDestroy();
        }
        super.onDestroy();
    }

    private void buildUi() {
        int pad = dp(12);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(pad, pad, pad, pad);

        TextView banner = new TextView(this);
        banner.setText("Rose Models: text chat with a local agent");
        banner.setTypeface(Typeface.DEFAULT_BOLD);
        root.addView(banner);

        transcript = new TextView(this);
        transcript.setTextIsSelectable(true);
        scroller = new ScrollView(this);
        scroller.addView(transcript);
        root.addView(scroller, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        actionCard = new TextView(this);
        actionCard.setPadding(0, pad / 2, 0, pad / 2);
        actionCard.setText("No app action yet.");
        root.addView(actionCard);

        status = new TextView(this);
        root.addView(status);

        input = new EditText(this);
        input.setHint("Message");
        root.addView(input);

        LinearLayout row1 = new LinearLayout(this);
        send = button("Send", v -> {
            String text = input.getText().toString();
            input.setText("");
            if (host != null) {
                host.send(text);
            }
        });
        stop = button("Stop", v -> {
            if (host != null) {
                host.stop();
            }
        });
        resume = button("Continue", v -> {
            if (host != null) {
                host.continueTurn();
            }
        });
        for (Button b : new Button[] {send, stop, resume}) {
            row1.addView(b, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        }
        root.addView(row1);
        LinearLayout row2 = new LinearLayout(this);
        for (Button b : new Button[] {button("Apps", v -> chooseApps()), button("Settings", v -> editSettings())}) {
            row2.addView(b, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        }
        root.addView(row2);
        setContentView(root);
    }

    private Button button(String label, View.OnClickListener listener) {
        Button b = new Button(this);
        b.setText(label);
        b.setOnClickListener(listener);
        return b;
    }

    private boolean busy() {
        if (host != null && host.hasActiveTurn()) {
            setStatus("Stop the current turn first.");
            return true;
        }
        return false;
    }

    /**
     * Service sign-in and model. The service is fixed; the model is one of ModelsService.Model, switched
     * explicitly (a new conversation; sign-in is per service and unchanged). Reset deletes this install's
     * device key so the next message enrolls a new one.
     */
    private void editSettings() {
        if (busy()) {
            return;
        }
        final ModelsService.Model other = model == ModelsService.Model.MUSE ? ModelsService.Model.QWEN
                : ModelsService.Model.MUSE;
        new AlertDialog.Builder(this)
                .setTitle("Rose Models service")
                .setMessage(ModelsService.ORIGIN + "\nModel: " + model.label + " (" + model.id + ")\nSign-in: "
                        + signInText())
                .setPositiveButton("Close", null)
                .setNegativeButton("Use " + other.label, (d, w) -> {
                    prefs().edit().putString(KEY_MODEL, other.id).apply();
                    recreate();
                })
                .setNeutralButton("Reset sign-in", (d, w) -> HttpsInferenceClient.background(() -> {
                    try {
                        auth.reset();
                    } catch (Exception e) {
                        main.post(() -> setStatus(AuthMessages.text("auth_key_unavailable")));
                        return;
                    }
                    main.post(this::connect);
                }))
                .show();
    }

    private String signInText() {
        switch (auth.state()) {
            case ENROLLED:
                return "this phone is enrolled";
            case PENDING:
                return "enrolling";
            case INVITE_NEEDED:
                return "scan the admin's sign-in QR code";
            case REVOKED:
                return "revoked";
            case REFUSED:
                return "refused";
            default:
                return "not yet enrolled";
        }
    }

    /** Signs in in the background at start; a problem is shown but never blocks Send or Settings. */
    private void connect() {
        setStatus("Connecting to " + model.label + "…");
        final DeviceAuth current = auth;
        HttpsInferenceClient.background(() -> {
            String problem = null;
            try {
                current.current(new CredentialSource.Cancel() {
                    @Override
                    public boolean isCancelled() {
                        return isFinishing() || isDestroyed();
                    }

                    @Override
                    public void track(java.net.HttpURLConnection connection) {
                    }
                });
            } catch (CredentialSource.AuthFailure f) {
                problem = f.isCancelled() ? null : AuthMessages.text(f.reason);
            }
            final String shown = problem;
            main.post(() -> {
                if (host != null && !host.hasActiveTurn()) {
                    if (shown == null) {
                        showReadyStatus();
                    } else {
                        setStatus(shown);
                    }
                }
            });
        });
    }

    private static DeviceAuth.Store store(final SharedPreferences prefs) {
        return new DeviceAuth.Store() {
            @Override
            public String get(String key) {
                return prefs.getString(key, null);
            }

            @Override
            public void put(String key, String value) {
                prefs.edit().putString(key, value).commit();
            }

            @Override
            public void remove(String key) {
                prefs.edit().remove(key).commit();
            }
        };
    }

    /** Lets the user pick which launcher apps open_app may open. Only this list is allowed. */
    private void chooseApps() {
        if (busy()) {
            return;
        }
        final List<AppAllowlist.Launchable> installed = new AndroidAppLauncher(this).launcherActivities();
        Collections.sort(installed, (a, b) -> a.label.compareToIgnoreCase(b.label));
        final String[] labels = new String[installed.size()];
        final boolean[] checked = new boolean[installed.size()];
        List<String> selectedPackages = new ArrayList<>();
        for (AppAllowlist.Entry e : allowlist.entries()) {
            selectedPackages.add(e.packageName);
        }
        for (int i = 0; i < installed.size(); i++) {
            labels[i] = installed.get(i).label;
            checked[i] = selectedPackages.contains(installed.get(i).packageName);
        }
        new AlertDialog.Builder(this)
                .setTitle("Apps Rose Models may open")
                .setMultiChoiceItems(labels, checked, (d, which, isChecked) -> checked[which] = isChecked)
                .setPositiveButton("Save", (d, w) -> {
                    List<AppAllowlist.Launchable> picked = new ArrayList<>();
                    for (int i = 0; i < installed.size(); i++) {
                        if (checked[i]) {
                            picked.add(installed.get(i));
                        }
                    }
                    allowlist = AppAllowlist.fromSelection(picked);
                    prefs().edit().putString(KEY_ALLOWLIST, allowlist.serialize()).apply();
                    recreate();
                })
                .setNegativeButton("Cancel", null)
                .show();
    }

    private void showReadyStatus() {
        if (allowlist.isEmpty()) {
            setStatus("Ready. No apps selected, so the model cannot open any. Use Apps to choose.");
            return;
        }
        StringBuilder ids = new StringBuilder();
        for (AppAllowlist.Entry e : allowlist.entries()) {
            ids.append(ids.length() == 0 ? "" : ", ").append(e.appId);
        }
        setStatus("Ready. Apps the model may open: " + ids);
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, MODE_PRIVATE);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    @Override
    public void appendTranscript(String speaker, String text) {
        transcript.append((transcript.length() == 0 ? "" : "\n\n") + speaker + ": " + text);
        scroller.post(() -> scroller.fullScroll(View.FOCUS_DOWN));
    }

    @Override
    public void setStatus(String text) {
        status.setText(text);
    }

    @Override
    public void showActionResult(LaunchResult result) {
        String note = result.status == LaunchResult.Status.LAUNCH_REQUESTED
                ? " (requested; this app cannot see whether it opened)" : "";
        actionCard.setText("Last action: " + result + note);
    }

    @Override
    public void setControls(boolean canSend, boolean canStop, boolean canContinue) {
        if (send == null) {
            return;
        }
        send.setEnabled(canSend && setupProblem == null);
        stop.setEnabled(canStop);
        resume.setEnabled(canContinue);
    }
}
