# Rose Agent work row in Launcher3's fallback overview (#156), staged

Rose holds HOME, so Launcher3 Quickstep opens its fallback overview. Pinned Launcher3 47accce,
`OverviewComponentObserver`, picks the fallback when the default home is another launcher. These files add Rose
Agent's running work as a row above Launcher3's unchanged human-task carousel. The row scrolls on its own.

- **`RoseAgentWork.java`:** pure decisions. A task is agent work only when its display's system-recorded owner uid is
  the installed `dev.pixel.backgroundcontrol` uid for the current user. ComputerControl session displays are created
  by `VirtualDeviceImpl` with the session owner's package and uid. Selecting a card opens Rose's viewer only while
  that task is the agent display's current screen; otherwise the card says why.
- **`RoseAgentStrip.java`:** the view. It reads tasks with `ActivityTaskManager.getTasks`, which the configured
  recents component may already do, and display owners with `Display.getOwnerUid`. It opens
  `dev.pixel.backgroundcontrol/.AgentWorkspaceActivity`. No permission, provider read or invented card.
- **`../patches/rose-switcher-launcher3.patch`:** adds the row to `quickstep/res/layout/fallback_recents_activity.xml`.

**Not yet applied to builds.** Installing these needs three changes together:
1. Copy these two files to
   `os/overlays/packages/apps/Launcher3/quickstep/src/com/android/quickstep/fallback/`.
2. Register the patch in `scripts/apply-os-changes.py`, under `if a.background_policy:`:
   `projects['packages/apps/Launcher3'] = ['rose-switcher-launcher3.patch']`.
3. Add `packages/apps/Launcher3` to `os/devices/pixel10.json` `roseInclusion.projects`.

Step 3 rebinds the Pixel 10 contract, so Infra's retained Blazer EXEC002 receipt test must expect
`binding-contract`. A Launcher3 compile has not been run: `RoseAgentStrip` uses platform APIs that are not in the
public SDK.
