package dev.pixel.voice;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/**
 * Project 2: the Rose tasks the workspace can offer. They are the approved HiFi's own tasks
 * (prototypes/rose-hifi/HANDOFF-WORKSPACE.md, "Exact task identity"), phrased for a real phone. "Draft a reply to
 * Jamie" is left out because it belongs to one notification.
 *
 * Each task asks first on the confirm sheet, and its goal tells Rose to ask the owner before sending, sharing,
 * booking or buying anything. The target is Rose Agent's own app entry, which WorkspaceSources fills in. Each one
 * is within App Core's limits: label at most 60, goal at most 300, id matching task:[A-Za-z0-9._-]{1,48}. Pure.
 */
final class WorkspaceTaskTemplates {
 private WorkspaceTaskTemplates() {}
 static final List<WorkspaceSnapshot.Task> ALL=Collections.unmodifiableList(Arrays.asList(
  new WorkspaceSnapshot.Task("grocery","Add missing items to grocery list",
   "Ask Rose to check your grocery list and add what's missing?",
   "Check the owner's grocery list and add the items they are missing for their next meal. Ask the owner about anything unclear, and ask before buying, ordering or sending anything.",null),
  new WorkspaceSnapshot.Task("prep-notes","Summarise prep notes",
   "Ask Rose to summarise your prep notes?",
   "Find the owner's prep notes for their next meeting and write them a short summary. Ask before sharing or sending anything to anyone.",null),
  new WorkspaceSnapshot.Task("saturday","Plan a quiet Saturday",
   "Ask Rose to plan a quiet Saturday for you?",
   "Suggest a quiet plan for the owner's Saturday from what they already have on. Ask before booking, buying or messaging anyone.",null)));
}
