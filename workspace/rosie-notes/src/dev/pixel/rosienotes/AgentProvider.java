package dev.pixel.rosienotes;

import android.content.*;
import android.content.pm.PackageManager;
import android.content.pm.ProviderInfo;
import android.database.Cursor;
import android.net.Uri;
import android.os.*;
import org.json.JSONObject;
import java.util.Map;

/** Only the existing same-signer PhoneControl.app_command route. No general tool catalog. */
public final class AgentProvider extends ContentProvider {
 @Override public boolean onCreate() { return true; }
 @Override public Bundle call(String method, String operationId, Bundle extras) {
  if (getContext().getPackageManager().checkSignatures(android.os.Process.myUid(), Binder.getCallingUid()) != PackageManager.SIGNATURE_MATCH)
   throw new SecurityException("Caller signing identity does not match");
  Map<String, Object> result;
  try {
   String raw = extras == null ? "{}" : extras.getString("params", "{}");
   if (raw == null || raw.length() > 200000) throw new IllegalArgumentException();
   Object parsed = MiniJson.parse(raw);
   if (!(parsed instanceof Map)) throw new IllegalArgumentException();
   @SuppressWarnings("unchecked") Map<String, Object> params = (Map<String, Object>) parsed;
   // No operationId receipt cache: every read and retry verifies disk and exact scope.
   result = new LifeNoteStore(getContext().getNoBackupFilesDir(), this::readyWorkspace)
       .execute("capabilities".equals(method) ? "life_note_capabilities" : method, params);
  } catch (IllegalArgumentException e) {
   result = LifeNoteStore.error("invalid_request", "invalid_request", "Expected bounded private-note JSON object");
  } catch (Exception e) {
   result = LifeNoteStore.error("uncertain", "readback_required", "Private-note outcome is uncertain; read this exact task and workspace");
  }
  if ("life_note_put".equals(method) && "verified".equals(result.get("status"))) MainActivity.notesChanged();
  Bundle reply = new Bundle(); reply.putString("result", new JSONObject(result).toString()); return reply;
 }
 private boolean readyWorkspace(String task, String workspace, String controllerTask) {
  long before = SystemClock.elapsedRealtime();
  try {
   PackageManager pm = getContext().getPackageManager();
   ProviderInfo provider = pm.resolveContentProvider(WorkspaceBinding.CONTROLLER + ".agent", 0);
   if (provider == null || !WorkspaceBinding.CONTROLLER.equals(provider.packageName)
       || pm.checkSignatures(getContext().getPackageName(), provider.packageName) != PackageManager.SIGNATURE_MATCH) return false;
   Bundle args = new Bundle(); args.putString("params", new JSONObject().put("sessionId", workspace).toString());
   Bundle reply = getContext().getContentResolver().call(Uri.parse("content://" + WorkspaceBinding.CONTROLLER + ".agent"), "get_state", null, args);
   if (reply == null || SystemClock.elapsedRealtime() - before > 3000) return false;
   Object state = MiniJson.parse(reply.getString("result", "{}"));
   return state instanceof Map && WorkspaceBinding.ready((Map<?, ?>) state, task, workspace, controllerTask);
  } catch (Exception e) { return false; }
 }
 @Override public Cursor query(Uri u, String[] p, String s, String[] a, String o) { throw new UnsupportedOperationException(); }
 @Override public String getType(Uri u) { return null; }
 @Override public Uri insert(Uri u, ContentValues v) { throw new UnsupportedOperationException(); }
 @Override public int delete(Uri u, String s, String[] a) { throw new UnsupportedOperationException(); }
 @Override public int update(Uri u, ContentValues v, String s, String[] a) { throw new UnsupportedOperationException(); }
}
