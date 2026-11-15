package dev.pixel.voice;

import android.content.Context;

/**
 * Nathan authorized server-side local inference in this session (2026-11-14). New installations use that
 * route through the existing paired, pinned link. An explicit compose-route-enabled=false opt-out survives.
 * Only the model location changes; no new device permission, pairing identity or server trust is introduced.
 */
final class WorkspaceDecisionGate {
 private WorkspaceDecisionGate() {}
 static final String KEY="compose-route-enabled";

 static boolean enabled(Context context){
  try{return context.getSharedPreferences(WorkspaceTaskGate.PREFS,Context.MODE_PRIVATE).getBoolean(KEY,true);}
  catch(RuntimeException e){return false;}
 }
 static WorkspaceTaskClient.Gate gate(Context context){return new WorkspaceTaskClient.Gate(enabled(context));}
}
