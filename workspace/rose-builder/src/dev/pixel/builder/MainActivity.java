package dev.pixel.builder;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageInstaller;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import java.io.File;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Project 1, Level 1: the app builder's screens. Library of the owner's apps → describe a new app → honest build
 * progress with Cancel → install, update or open each version through Android's installer → request changes → share.
 * Plain Android views and the platform Material theme, so it runs unchanged on ordinary Android 8+ phones; nothing here
 * needs RoseOS. Talks only to {@link BuilderApi}: the owner's paired Rose builder over rose-auth-v1
 * ({@link HttpsBuilderApi}), or, when the owner chooses it before pairing, the labelled in-memory demo. Every builder
 * call runs off the main thread; a screen's answer is drawn only if the owner is still on that screen.
 */
public final class MainActivity extends Activity {
 static final long POLL_MS = 1500;
 BuilderApi api;
 boolean demo;
 /** Bumped on every navigation, so a late answer for a screen the owner has left is dropped, never drawn. */
 int screen;
 /** A builder call in flight from a button: further taps wait for it. */
 boolean busy;
 final Handler main = new Handler(Looper.getMainLooper());
 final ExecutorService io = Executors.newSingleThreadExecutor();
 LinearLayout root;
 String openProject;
 /** Jobs the owner asked to stop; they read "Stopping…" until the job itself reports its end. */
 final java.util.Set<String> stopRequested = new java.util.HashSet<>();
 /** One clientRequestId per create or change the owner asks for, reused when it's retried after a lost connection,
  * so a retry never starts a second build. Cleared once the builder answers. */
 final RequestIds createIds = new RequestIds(() -> java.util.UUID.randomUUID().toString()),
   reviseIds = new RequestIds(() -> java.util.UUID.randomUUID().toString());
 /** The builder's notes for a project's newest request, shown on its screen until the owner leaves it; and a
  * refusal of a change, shown above the change box. Plain text only. */
 final java.util.Map<String, List<String>> notesFor = new java.util.HashMap<>();
 final java.util.Map<String, String> refusalFor = new java.util.HashMap<>();
 final Runnable poll = new Runnable() { @Override public void run() { if (openProject != null) showProject(openProject); } };

 interface Work<T> { T run() throws Exception; }
 interface Done<T> { void got(T t); }
 interface Failed { void failed(Exception e); }

 /** Runs a builder call on the io thread; the answer is drawn on the main thread only if its screen is still shown. */
 <T> void async(Work<T> work, Done<T> done, Failed failed) { async(work, done, failed, false); }

 private <T> void async(Work<T> work, Done<T> done, Failed failed, boolean fromButton) {
  int at = screen;
  io.execute(() -> {
   T r = null; Exception err = null;
   try { r = work.run(); } catch (Exception e) { err = e; }
   T result = r; Exception error = err;
   main.post(() -> {
    if (fromButton) busy = false;
    if (isDestroyed() || at != screen) return;
    if (error == null) done.got(result); else failed.failed(error);
   });
  });
 }

 /** A button's call: one at a time. */
 <T> void act(Work<T> work, Done<T> done, Failed failed) {
  if (busy) return;
  busy = true;
  async(work, done, failed, true);
 }

 /** Paired: the real builder. Not paired: the pairing screen, which also offers the labelled demo. */
 boolean connect() {
  if (api != null) return true;
  Pairing p = Pairing.load(this);
  if (p == null) return false;
  try { api = new HttpsBuilderApi(this, p); return true; }
  catch (java.security.GeneralSecurityException e) { Pairing.forget(this); return false; }
 }

 @Override protected void onCreate(Bundle state) {
  super.onCreate(state);
  ScrollView scroll = new ScrollView(this);
  root = new LinearLayout(this);
  root.setOrientation(LinearLayout.VERTICAL);
  int pad = dp(20);
  root.setPadding(pad, pad, pad, pad);
  scroll.addView(root);
  setContentView(scroll);
  if (state != null) { openProject = state.getString("project"); demo = state.getBoolean("demo"); }
  if (demo) api = new FakeBuilderApi();
  if (!connect()) showPair(null);
  else if (openProject != null) showProject(openProject); else showLibrary();
  // Android may report an install after this activity was destroyed: the report then starts a new one.
  if (state == null && Installer.ACTION_STATUS.equals(getIntent().getAction())) installStatus(getIntent());
 }

 @Override protected void onSaveInstanceState(Bundle out) { super.onSaveInstanceState(out); out.putString("project", openProject); out.putBoolean("demo", demo); }
 @Override protected void onDestroy() { main.removeCallbacks(poll); io.shutdownNow(); super.onDestroy(); }
 @Override public void onBackPressed() { if (openProject != null && api != null) showLibrary(); else super.onBackPressed(); }

 /** Android's installer reports here; a confirmation it needs is shown to the owner, never skipped. */
 @Override protected void onNewIntent(Intent intent) {
  super.onNewIntent(intent);
  if (Installer.ACTION_STATUS.equals(intent.getAction())) installStatus(intent);
 }

 void installStatus(Intent intent) {
  int status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
  if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
   Intent confirm = intent.getParcelableExtra(Intent.EXTRA_INTENT);
   if (confirm != null) startActivity(confirm);
   return;
  }
  if (status == PackageInstaller.STATUS_SUCCESS) toast("Installed");
  else if (status == PackageInstaller.STATUS_FAILURE_ABORTED) toast("Install cancelled");
  else toast("Android didn't install it: " + ShareMessage.clean(intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE), "no reason given"));
  // Report what Android actually did, with the signer the phone now has for the package, never what was intended.
  String project = intent.getStringExtra(Installer.EXTRA_PROJECT), pkg = intent.getStringExtra(PackageInstaller.EXTRA_PACKAGE_NAME);
  if (project != null && pkg != null) {
   InstallPlan.Installed now = Installer.installed(this, pkg);
   InstallPlan.Receipt outcome = InstallPlan.androidOutcome(status == PackageInstaller.STATUS_SUCCESS,
     status == PackageInstaller.STATUS_FAILURE_ABORTED, intent.getBooleanExtra(Installer.EXTRA_WAS_INSTALLED, false));
   String detail = outcome == InstallPlan.Receipt.FAILED ? ShareMessage.clean(intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE), null) : null;
   report(new BuilderApi.InstallReceipt(project, pkg, intent.getLongExtra(Installer.EXTRA_VERSION, -1), now == null ? null : now.signerSha256, outcome, detail));
  }
  if (openProject != null && api != null) showProject(openProject);
 }

 void report(BuilderApi.InstallReceipt r) {
  BuilderApi a = api;
  if (a == null) return;
  io.execute(() -> { try { a.reportInstall(r); } catch (BuilderApi.Unreachable e) { main.post(() -> toast("Your builder couldn't be told the install result yet")); } });
 }

 /** Pairing: the owner makes a pairing code on their Mac and pastes its link here. */
 void showPair(String error) {
  screen++;
  openProject = null;
  main.removeCallbacks(poll);
  root.removeAllViews();
  title("Connect to your Rose builder");
  body("Apps are built on your Mac, not on this phone. On your Mac, make a Rose pairing code, copy its link (it starts with roseagent://pair) and paste it here. Your phone and Mac both need Tailscale.");
  if (error != null) body(error);
  EditText link = input("roseagent://pair?…");
  button("Connect", x -> {
   String t = link.getText().toString().trim();
   if (t.isEmpty()) { toast("Paste the pairing link first"); return; }
   act(() -> Pairing.pair(this, t), p -> {
    try { api = new HttpsBuilderApi(this, p); demo = false; showLibrary(); }
    catch (java.security.GeneralSecurityException e) { showPair("This phone couldn't use its pairing key. Try again."); }
   }, e -> showPair(e instanceof Pairing.Failure ? e.getMessage() : "Couldn't connect. Try again."));
  });
  button("Try the demo instead", x -> { api = new FakeBuilderApi(); demo = true; showLibrary(); });
 }

 void showLibrary() {
  openProject = null;
  // Notes and refusals belong to the request just made; leaving the project ends them.
  notesFor.clear();
  refusalFor.clear();
  main.removeCallbacks(poll);
  screen++;
  root.removeAllViews();
  title(demo ? "Your apps (demo)" : "Your apps");
  if (demo) body("This is a demo: nothing is built and nothing can be installed. Connect to your Rose builder to make real apps.");
  body("Loading…");
  async(api::projects, this::drawLibrary, e -> { root.removeAllViews(); unreachable(e, this::showLibrary); });
 }

 void drawLibrary(List<BuilderApi.Project> projects) {
  root.removeAllViews();
  title(demo ? "Your apps (demo)" : "Your apps");
  if (demo) body("This is a demo: nothing is built and nothing can be installed.");
  if (projects.isEmpty()) body("Describe an app and Rose builds it on your Mac or builder. Nothing is built on this phone.");
  for (BuilderApi.Project p : projects) {
   BuilderApi.Version v = p.latest();
   String line = p.name + "\n" + (p.job != null && !p.job.terminal() ? BuildJob.status(p.job) : v == null ? BuildJob.status(p.job) : "Version " + v.versionName);
   button(line, x -> showProject(p.projectId));
  }
  button("New app", x -> showCreate(null, null));
  if (demo) button("Connect to your Rose builder", x -> { api = null; demo = false; showPair(null); });
 }

 /** prefill keeps the owner's words after a refusal; refusal is the builder's own sentence, shown as plain text. */
 void showCreate(String prefill, String refusal) {
  main.removeCallbacks(poll);
  screen++;
  root.removeAllViews();
  title("New app");
  body("Say what the app should do. You can ask for changes after it's built.");
  if (refusal != null) body(ShareMessage.clean(refusal, "Your builder didn't start this app. Nothing was started."));
  EditText text = input("A tip calculator that splits the bill");
  if (prefill != null) text.setText(prefill);
  button("Build it", x -> {
   String d = text.getText().toString().trim();
   if (d.isEmpty()) { toast("Describe the app first"); return; }
   String rid = createIds.forText(d);
   act(() -> api.create(rid, d), s -> {
    createIds.done();
    notesFor.put(s.project.projectId, s.notes);
    showProject(s.project.projectId);
   }, e -> {
    if (e instanceof BuilderApi.OutOfScope) { createIds.done(); showCreate(d, e.getMessage()); }
    else if (e instanceof BuilderApi.Refused) { createIds.done(); showCreate(d, "Your builder didn't start this app (" + ShareMessage.clean(e.getMessage(), "refused") + "). Nothing was started."); }
    else if (pairAgain(e)) return;
    else toast("Can't reach your builder. Your description is kept; try again.");
   });
  });
  button("Back", x -> showLibrary());
 }

 /** Shows a project; a poll refreshes it in place (the screen isn't cleared until the new answer arrives). */
 void showProject(String id) {
  boolean refresh = id.equals(openProject) && root.getChildCount() > 0;
  openProject = id;
  main.removeCallbacks(poll);
  // A refresh of the same project is the same screen: a button's answer still in flight is kept.
  if (!refresh) { screen++; root.removeAllViews(); body("Loading…"); }
  async(() -> api.project(id), p -> drawProject(id, p), e -> { root.removeAllViews(); unreachable(e, () -> showProject(id)); });
 }

 void drawProject(String id, BuilderApi.Project p) {
  root.removeAllViews();
  if (p == null) { showLibrary(); return; }
  title(p.name);
  // The builder's own notes about this request: plain text, never markup or links.
  List<String> notes = notesFor.get(id);
  if (notes != null) for (String n : notes) { String c = ShareMessage.clean(n, null); if (c != null) body(c); }
  if (p.job != null) {
   body(BuildJob.status(p.job, stopRequested.contains(p.job.jobId)));
   if (p.job.stage == BuildJob.Stage.FAILED) {
    if (p.job.failure != null && p.job.failure.summary != null) body(p.job.failure.summary);
    body(BuildJob.failureAdvice(p.job.failure));
    // Only when the builder says a retry can help and no newer version exists; it runs the same job again.
    if (BuildJob.offerRetry(p.job, p.newestVersionAt())) button("Try again", x -> act(() -> api.retry(p.job.jobId), n -> showProject(id), e -> {
     if (pairAgain(e)) return;
     toast(e instanceof BuilderApi.Refused ? "That build can't be retried now; showing the latest" : "Can't reach your builder");
     showProject(id);
    }));
   }
   if (p.job.cancellable()) {
    if (!stopRequested.contains(p.job.jobId)) button("Cancel build", x -> act(() -> api.cancel(p.job.jobId), c -> {
     if (c == BuilderApi.CancelStatus.ACCEPTED) stopRequested.add(p.job.jobId);
     else toast(c == BuilderApi.CancelStatus.NOT_RUNNING ? "That build had already finished" : "Your builder doesn't know that build");
     showProject(id);
    }, e -> { if (!pairAgain(e)) toast("Can't reach your builder to cancel"); }));
    main.postDelayed(poll, POLL_MS);
   }
  }
  InstallPlan.Installed installed = Installer.installed(this, p.packageName);
  for (int i = p.versions.size() - 1; i >= 0; i--) {
   BuilderApi.Version v = p.versions.get(i);
   InstallPlan offer = InstallPlan.offer(new InstallPlan.Apk(p.packageName, p.signerSha256, v.apkSha256, v.versionCode), installed);
   if (v.notes != null) body("Version " + v.versionName + ": " + ShareMessage.clean(v.notes, ""));
   if (offer.action == InstallPlan.Action.REFUSE) { body("Version " + v.versionName + ": " + offer.reason); continue; }
   if (offer.action == InstallPlan.Action.OPEN) { button("Open version " + v.versionName, x -> open(p.packageName)); continue; }
   if (v.apkSha256 == null) { body("Version " + v.versionName + " isn't downloadable yet."); continue; }
   button(InstallPlan.label(offer) + " version " + v.versionName, x -> download(p, v, installed));
  }
  // One active job per project: a change can be asked whenever none is queued or running, including after a failed
  // first build (the builder starts again from the original description plus the change).
  if (p.job == null || p.job.terminal()) {
   String refused = refusalFor.get(id);
   if (refused != null) body(refused);
   EditText change = input("What should change?");
   button("Request change", x -> {
    String c = change.getText().toString().trim();
    if (c.isEmpty()) { toast("Describe the change first"); return; }
    String rid = reviseIds.forText(c);
    act(() -> api.revise(p.projectId, rid, c), s -> {
     reviseIds.done();
     refusalFor.remove(id);
     notesFor.put(id, s.notes);
     showProject(id);
    }, e -> {
     if (e instanceof BuilderApi.OutOfScope || e instanceof BuilderApi.Refused) {
      reviseIds.done();
      refusalFor.put(id, e instanceof BuilderApi.OutOfScope ? ShareMessage.clean(e.getMessage(), "Your builder didn't make this change. Nothing was started.")
        : "Your builder didn't make this change (" + ShareMessage.clean(e.getMessage(), "refused") + "). Nothing was started.");
      showProject(id);
     } else if (!pairAgain(e)) toast("Can't reach your builder. Try again; it won't start a second build.");
    });
   });
  }
  if (p.latest() != null) button("Share", x -> share(p));
  button("All apps", x -> showLibrary());
 }

 /** Downloads one version (HTTPS only), checks it is exactly what the builder recorded, then asks Android. */
 void download(BuilderApi.Project p, BuilderApi.Version v, InstallPlan.Installed installed) {
  if (!Installer.allowed(this)) {
   toast("Allow this app to install apps, then come back");
   startActivity(Installer.allowIntent(this));
   return;
  }
  toast("Downloading…");
  io.execute(() -> {
   File f = new File(getCacheDir(), "download.apk");
   String error = null;
   try {
    if (demo) throw new java.io.FileNotFoundException("demo");
    api.download(v.apkSha256, v.apkSize, f);
    InstallPlan.Apk expected = new InstallPlan.Apk(p.packageName, p.signerSha256, v.apkSha256, v.versionCode);
    // A file of the wrong length is refused as a mismatch without parsing it.
    InstallPlan plan = InstallPlan.verify(expected, f.length() == v.apkSize ? Installer.inspect(this, f) : null, installed);
    if (plan.action == InstallPlan.Action.REFUSE) {
     error = plan.reason;
     // Refused before Android saw it: the builder is told, with the signer the phone has now.
     report(new BuilderApi.InstallReceipt(p.projectId, p.packageName, v.versionCode, installed == null ? null : installed.signerSha256, plan.refusal, null));
    } else if (plan.action != InstallPlan.Action.OPEN) Installer.install(this, f, p.packageName, p.projectId, v.versionCode, installed != null);
   } catch (Exception e) {
    // The exception's class only: a message could carry a token-bearing URL.
    error = "The download failed (" + e.getClass().getSimpleName() + "). Check your connection and try again.";
   }
   // Android's session holds its own copy once committed; the download is never kept.
   f.delete();
   String shown = error;
   if (shown != null) main.post(() -> toast(shown));
  });
 }

 void open(String packageName) {
  Intent launch = getPackageManager().getLaunchIntentForPackage(packageName);
  if (launch == null) toast("That app has no screen to open"); else startActivity(launch);
 }

 /** The owner picks recipients in Android's share sheet or SMS composer; nothing is sent from here. */
 void share(BuilderApi.Project p) {
  act(() -> { String u = api.shareUrl(p.projectId, null); return u == null ? "" : u; }, url -> {
   if (url.isEmpty()) toast("Sharing needs a download link, which isn't available yet");
   else shareLink(p, url);
  }, e -> toast("Can't reach your builder"));
 }

 void shareLink(BuilderApi.Project p, String url) {
  String text = ShareMessage.text(p.name, p.latest().versionName, url, true);
  Intent send = new Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text);
  Intent sms = new Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:")).putExtra("sms_body", text);
  Intent chooser = Intent.createChooser(send, "Share " + p.name);
  if (sms.resolveActivity(getPackageManager()) != null) chooser.putExtra(Intent.EXTRA_INITIAL_INTENTS, new Intent[]{sms});
  try { startActivity(chooser); } catch (ActivityNotFoundException e) { toast("No app on this phone can share it"); }
 }

 /** A final refusal: the builder no longer knows this phone. The owner is asked to pair again. */
 boolean pairAgain(Exception e) {
  if (!(e instanceof BuilderApi.Unreachable) || !((BuilderApi.Unreachable) e).pairAgain) return false;
  api = null;
  Pairing.forget(this);
  showPair("Your Rose builder no longer knows this phone. Make a new pairing code on your Mac and paste it here.");
  return true;
 }

 void unreachable(Exception e, Runnable retry) {
  if (pairAgain(e)) return;
  if (e instanceof BuilderApi.Unreachable && "clock".equals(e.getMessage())) {
   title("Your Rose builder didn't accept this phone's request");
   body("Check that this phone's date and time are set automatically, then try again.");
   button("Try again", x -> retry.run());
   return;
  }
  BuildJob.Failure f = new BuildJob.Failure(BuildJob.Category.UNREACHABLE, null, true);
  title(BuildJob.failureTitle(f));
  body(BuildJob.failureAdvice(f));
  button("Reconnect", x -> retry.run());
 }

 void title(String s) { TextView t = text(s, 24); t.setPadding(0, 0, 0, dp(12)); }
 void body(String s) { TextView t = text(s, 16); t.setPadding(0, dp(4), 0, dp(8)); }
 TextView text(String s, int sp) { TextView t = new TextView(this); t.setText(s); t.setTextSize(sp); root.addView(t); return t; }
 EditText input(String hint) {
  EditText e = new EditText(this);
  e.setHint(hint);
  e.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
  e.setMinLines(2);
  e.setGravity(Gravity.TOP | Gravity.START);
  root.addView(e);
  return e;
 }
 void button(String s, View.OnClickListener l) {
  Button b = new Button(this);
  b.setText(s);
  b.setAllCaps(false);
  b.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
  b.setOnClickListener(l);
  root.addView(b);
 }
 void toast(String s) { Toast.makeText(this, s, Toast.LENGTH_LONG).show(); }
 int dp(int v) { return Math.round(v * getResources().getDisplayMetrics().density); }
}
