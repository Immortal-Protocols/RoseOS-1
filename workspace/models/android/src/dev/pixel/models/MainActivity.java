package dev.pixel.models;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
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
 * (NativeAgent); the configured HTTPS endpoint is used only for inference. There is no default
 * endpoint or model: until both are set in Settings, Send stays disabled.
 */
public final class MainActivity extends Activity implements TurnView {
    private static final String PREFS = "rose-models";
    private static final String KEY_ALLOWLIST = "allowlist";
    private static final String KEY_ENDPOINT = "endpoint";
    private static final String KEY_MODEL = "model";

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

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        SharedPreferences prefs = prefs();
        allowlist = AppAllowlist.parse(prefs.getString(KEY_ALLOWLIST, ""));
        String endpoint = prefs.getString(KEY_ENDPOINT, "");
        String model = prefs.getString(KEY_MODEL, "").trim();
        buildUi();
        setupProblem = EndpointPolicy.check(endpoint);
        if (setupProblem == null && model.isEmpty()) {
            setupProblem = "no model id is configured";
        }
        if (setupProblem != null) {
            setStatus("Setup needed: " + setupProblem + ". Use Settings.");
            setControls(false, false, false);
            return;
        }
        NativeAgent agent;
        try {
            agent = new NativeAgent(AgentConfigJson.build(model, AgentConfigJson.DEFAULT_SYSTEM_PROMPT, allowlist));
        } catch (IllegalArgumentException e) {
            setupProblem = "the agent rejected the settings: " + e.getMessage();
            setStatus("Setup needed: " + setupProblem);
            setControls(false, false, false);
            return;
        }
        host = new LocalAgentHost(agent, new HttpsInferenceClient(endpoint),
                new AllowlistLauncher(allowlist, new AndroidAppLauncher(this)), this, main::post,
                (millis, task) -> main.postDelayed(task, millis));
        showReadyStatus();
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

    /** HTTPS endpoint and exact served model id. Nothing is pre-filled. */
    private void editSettings() {
        if (busy()) {
            return;
        }
        final EditText endpoint = new EditText(this);
        endpoint.setHint("https://… inference URL (POST)");
        endpoint.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        endpoint.setText(prefs().getString(KEY_ENDPOINT, ""));
        final EditText model = new EditText(this);
        model.setHint("Exact model id served by the endpoint");
        model.setText(prefs().getString(KEY_MODEL, ""));
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        form.addView(endpoint);
        form.addView(model);
        new AlertDialog.Builder(this)
                .setTitle("Inference settings")
                .setView(form)
                .setPositiveButton("Save", (d, w) -> {
                    prefs().edit()
                            .putString(KEY_ENDPOINT, endpoint.getText().toString().trim())
                            .putString(KEY_MODEL, model.getText().toString().trim())
                            .apply();
                    recreate();
                })
                .setNegativeButton("Cancel", null)
                .show();
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
