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
 * needs RoseOS. Talks only to {@link BuilderApi} (the in-memory fake until App Core's service exists).
 */
public final class MainActivity extends Activity {
 static final long POLL_MS = 1500;
 final BuilderApi api = new FakeBuilderApi();
 final Handler main = new Handler(Looper.getMainLooper());
 final ExecutorService io = Executors.newSingleThreadExecutor();
 LinearLayout root;
 String openProject;
 /** Jobs the owner asked to stop; they read "Stopping…" until the job itself reports its end. */
 final java.util.Set<String> stopRequested = new java.util.HashSet<>();
 /** One clientRequestId per create or change the owner asks for, reused when it's retried after a lost connection,
  * so a retry never starts a second build. Cleared once the builder answers. */
 String createRequest, reviseRequest;
 final Runnable poll = new Runnable() { @Override public void run() { if (openProject != null) showProject(openProject); } };

 @Override protected void onCreate(Bundle state) {
  super.onCreate(state);
  ScrollView scroll = new ScrollView(this);
  root = new LinearLayout(this);
  root.setOrientation(LinearLayout.VERTICAL);
  int pad = dp(20);
  root.setPadding(pad, pad, pad, pad);
  scroll.addView(root);
  setContentView(scroll);
  if (state != null) openProject = state.getString("project");
  if (openProject != null) showProject(openProject); else showLibrary();
  // Android may report an install after this activity was destroyed: the report then starts a new one.
  if (state == null && Installer.ACTION_STATUS.equals(getIntent().getAction())) installStatus(getIntent());
 }

 @Override protected void onSaveInstanceState(Bundle out) { super.onSaveInstanceState(out); out.putString("project", openProject); }
 @Override protected void onDestroy() { main.removeCallbacks(poll); io.shutdownNow(); super.onDestroy(); }
 @Override public void onBackPressed() { if (openProject != null) showLibrary(); else super.onBackPressed(); }

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
  if (openProject != null) showProject(openProject);
 }

 void report(BuilderApi.InstallReceipt r) {
  io.execute(() -> { try { api.reportInstall(r); } catch (BuilderApi.Unreachable e) { main.post(() -> toast("Your builder couldn't be told the install result yet")); } });
 }

 void showLibrary() {
  openProject = null;
  main.removeCallbacks(poll);
  root.removeAllViews();
  title("Your apps");
  List<BuilderApi.Project> projects;
  try { projects = api.projects(); } catch (BuilderApi.Unreachable e) { unreachable(this::showLibrary); return; }
  if (projects.isEmpty()) body("Describe an app and Rose builds it on your Mac or builder. Nothing is built on this phone.");
  for (BuilderApi.Project p : projects) {
   BuilderApi.Version v = p.latest();
   String line = p.name + "\n" + (p.job != null && !p.job.terminal() ? BuildJob.status(p.job) : v == null ? BuildJob.status(p.job) : "Version " + v.versionName);
   button(line, x -> showProject(p.projectId));
  }
  button("New app", x -> showCreate());
 }

 void showCreate() {
  main.removeCallbacks(poll);
  root.removeAllViews();
  title("New app");
  body("Say what the app should do. You can ask for changes after it's built.");
  EditText text = input("A tip calculator that splits the bill");
  button("Build it", x -> {
   String d = text.getText().toString().trim();
   if (d.isEmpty()) { toast("Describe the app first"); return; }
   if (createRequest == null) createRequest = java.util.UUID.randomUUID().toString();
   try { String pid = api.create(createRequest, d).projectId; createRequest = null; showProject(pid); }
   catch (BuilderApi.Unreachable e) { toast("Can't reach your builder. Your description is kept; try again."); }
  });
  button("Back", x -> showLibrary());
 }

 void showProject(String id) {
  openProject = id;
  main.removeCallbacks(poll);
  BuilderApi.Project p;
  try { p = api.project(id); } catch (BuilderApi.Unreachable e) { root.removeAllViews(); unreachable(() -> showProject(id)); return; }
  root.removeAllViews();
  if (p == null) { showLibrary(); return; }
  title(p.name);
  if (p.job != null) {
   body(BuildJob.status(p.job, stopRequested.contains(p.job.jobId)));
   if (p.job.stage == BuildJob.Stage.FAILED) {
    if (p.job.failure != null && p.job.failure.summary != null) body(p.job.failure.summary);
    body(BuildJob.failureAdvice(p.job.failure));
    // Only when the builder says a retry can help; it runs the same job again as its next attempt.
    if (p.job.retryable()) button("Try again", x -> {
     try { api.retry(p.job.jobId); } catch (BuilderApi.Unreachable e) { toast("Can't reach your builder"); }
     showProject(id);
    });
   }
   if (p.job.cancellable()) {
    if (!stopRequested.contains(p.job.jobId)) button("Cancel build", x -> {
     try {
      BuilderApi.CancelStatus c = api.cancel(p.job.jobId);
      if (c == BuilderApi.CancelStatus.ACCEPTED) stopRequested.add(p.job.jobId);
      else toast(c == BuilderApi.CancelStatus.NOT_RUNNING ? "That build had already finished" : "Your builder doesn't know that build");
     } catch (BuilderApi.Unreachable e) { toast("Can't reach your builder to cancel"); }
     showProject(id);
    });
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
  // Changes build on a delivered version; a first build that failed is retried, not revised.
  if ((p.job == null || p.job.terminal()) && p.latest() != null) {
   EditText change = input("What should change?");
   button("Request change", x -> {
    String c = change.getText().toString().trim();
    if (c.isEmpty()) { toast("Describe the change first"); return; }
    if (reviseRequest == null) reviseRequest = java.util.UUID.randomUUID().toString();
    try { api.revise(p.projectId, reviseRequest, c); reviseRequest = null; }
    catch (BuilderApi.Unreachable e) { toast("Can't reach your builder. Try again; it won't start a second build."); return; }
    showProject(id);
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
  String url;
  try { url = api.shareUrl(p.projectId, null); } catch (BuilderApi.Unreachable e) { toast("Can't reach your builder"); return; }
  if (url == null) { toast("Sharing needs a download link, which isn't available yet"); return; }
  String text = ShareMessage.text(p.name, p.latest().versionName, url, true);
  Intent send = new Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text);
  Intent sms = new Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:")).putExtra("sms_body", text);
  Intent chooser = Intent.createChooser(send, "Share " + p.name);
  if (sms.resolveActivity(getPackageManager()) != null) chooser.putExtra(Intent.EXTRA_INITIAL_INTENTS, new Intent[]{sms});
  try { startActivity(chooser); } catch (ActivityNotFoundException e) { toast("No app on this phone can share it"); }
 }

 void unreachable(Runnable retry) {
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
