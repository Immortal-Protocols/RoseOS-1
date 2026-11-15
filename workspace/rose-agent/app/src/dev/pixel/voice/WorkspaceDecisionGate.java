package dev.pixel.voice;

import android.content.Context;

/**
 * Project 2: whether the workspace asks the Rose server for its decisions (WorkspaceDecisionClient). It is off
 * unless the "compose-route-enabled" preference is set explicitly, and nothing in source sets it. Turning it on
 * sends the phone's snapshot to the owner's own Rose server: the labels of apps, shortcuts and templates, and,
 * for a prompt, the owner's words. That needs Nathan's decision first. While it is off, the page uses the
 * starter, which is not a model.
 */
final class WorkspaceDecisionGate {
 private WorkspaceDecisionGate() {}
 static final String KEY="compose-route-enabled";

 static boolean enabled(Context context){
  try{return context.getSharedPreferences(WorkspaceTaskGate.PREFS,Context.MODE_PRIVATE).getBoolean(KEY,false);}
  catch(RuntimeException e){return false;}
 }
 static WorkspaceTaskClient.Gate gate(Context context){return new WorkspaceTaskClient.Gate(enabled(context));}
}
