package dev.pixel.builder;

import java.util.List;

/**
 * When the builder's screens fetch again, so the phone never keeps showing a build state the server has moved past.
 * Pure, host-tested. The library and a project are read fresh when the owner comes back to the builder (resume, or
 * re-opening it from the workspace while it is still alive), and the library keeps polling while any build in it is
 * queued or running, as a project's own screen already does. Screens the owner is typing in are never refetched.
 */
final class Refresh {
 private Refresh() {}

 /** What is on screen, as far as refreshing goes. */
 enum Shown { OTHER, LIBRARY, PROJECT }

 /** What to fetch when the builder comes back to the foreground. */
 enum OnResume { NOTHING, LIBRARY, PROJECT }

 /**
  * firstResume: the resume that follows onCreate, which has just fetched. connected: the builder has an api to ask.
  * Only the library and a project are refetched; New app, sign-in and pairing keep what the owner has typed.
  */
 static OnResume onResume(Shown shown, boolean firstResume, boolean connected) {
  if (firstResume || !connected || shown == null) return OnResume.NOTHING;
  switch (shown) {
   case LIBRARY: return OnResume.LIBRARY;
   case PROJECT: return OnResume.PROJECT;
   default: return OnResume.NOTHING;
  }
 }

 /** The library keeps polling while any project in it has a build queued or running. */
 static boolean libraryPolls(List<BuilderApi.Project> projects) {
  if (projects == null) return false;
  for (BuilderApi.Project p : projects) if (p != null && p.job != null && !p.job.terminal()) return true;
  return false;
 }
}
