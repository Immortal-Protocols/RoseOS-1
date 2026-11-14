# Project 2 — Adaptive workspace page: HiFi prototype handoff

This is a prototype for review only. It is not native and nothing is published. Designer review and
Nathan's approval come before any native work.

- **Generation is simulated.** Every layout comes from a fixed set of compositions held on the phone,
  with a selectable delay. No model is called.
- **All content is sample data.** That covers apps, messages, recipes and calendar entries. The page
  says so in its header: "Simulated generation · sample data".

Both files are built from the same source, and Home, the dock (`#home-tray`) and the chin (`#home-bar`)
are unchanged:

- `Rose-Glacier.html` opens in Glacier.
- `Rose-HiFi.html` opens in Oxblood.

Classic has no workspace page.

**Overlap.** Codex WebGL built a separate Project 2 prototype. It is on hold and its artifact cannot be
reached (a Library 401 error). This build does not use that work, recreate it or replace it.

## Revisions

| Revision | Source | Status |
| --- | --- | --- |
| r1 | `8edd8181` (evidence `a55ebbe2`) | Delivered first. Kept unchanged for Nathan's review. |
| r2 | This revision (see `evidence/workspace-<commit>/`) | Answers the Designer source review of r1. |

r2 changes, one per review item:

1. **Reload persistence.** The layout, arrangement, pins, undo history (last 20), recent layouts, the
   echoed request and direct-action states are saved after every change and restored on reload.
   - Browser `localStorage` stands in for on-device storage.
   - Rose tasks belong to the task model and are not persisted, so after a reload they read as not
     started.
   - If storage is unavailable, the page still works and the harness says "not saved".
   - Harness → **Reset workspace** clears the saved state.
2. **Pins keep their position.** On Regenerate or a new request, a pinned card stays at the same index;
   only the unpinned cards around it move.
3. **Exact task identity.**
   - "Add missing items to grocery list" starts its own sample task with that exact title. Its windows
     are a Notes grocery list and a Firefox recipe.
   - "Summarise prep notes" likewise starts its own task, with prep notes and review files.
   - "Draft a reply to Jamie" and "Plan a quiet Saturday" use the existing tasks of the same names.
   - The two new tasks join the task registry only when the person confirms them (`registerTask`), so
     existing flows are unchanged.
   - The card's state is read from the task model: "Rose is working", "needs your choice" or
     "finished · in Rose's work". Tapping a started task opens Rose's work.
4. **Workspace navigation mark.** A four-point star sits before Home's round dots. It lights on the
   workspace while every Home dot is off. On Home it is outlined and unlit.
5. **"Show workspace" offer.** A request made from inside an app shows a status card at the top:
   - "Preparing workspace: … simulated" while it composes.
   - Then "Workspace ready: …" with **Show workspace** and Dismiss.
   - "Couldn't prepare workspace · kept your last layout" if it fails.

   It never leaves the app by itself. The card disappears once you reach the workspace.

r2 also fixes three things the new checks exposed:

- A newly registered task crashed the task announcer.
- The running timer replaced every card once a second, so a tap could land on a card as it was
  replaced. Now only the timer's label changes.
- The work view showed generic app content inside the new tasks' windows. They now show their own
  sample content.

## How to try it

1. Open either HTML file in a desktop browser. A phone frame and the review harness appear.
2. On Home, swipe right (drag right) to reach **Workspace**, a new page to the left of Home page 1.
   The harness button "Show workspace" also takes you there.
3. Ask for a workspace in one of three ways:
   - Tap **Arrange for me** to get a best guess.
   - Tap the field and type, for example "dinner", "work" or "unwind", then press the keyboard's action
     key.
   - Tap the mic. It listens for 1.2 s (simulated) and hears "Something relaxing for tonight".
4. On the cards:
   - **Pin** keeps a card first when you Regenerate or ask for something else.
   - **Regenerate** gives another arrangement of the same request.
   - **Undo** steps back through layouts and pins.
   - The **recent** chips reopen retained layouts.
5. Two kinds of action:
   - **Direct actions** (filled, labelled "Direct") act at once, for example the timer, play, focus,
     Do Not Disturb, dim and quick reply.
   - **Rose tasks** (outlined, with the pearl, labelled "Rose task · asks first") open a confirm sheet.
     "Ask Rose" starts real prototype Rose work, which then shows in Rose's work. "Not now" starts
     nothing.
6. Harness → Workspace:
   - Simulated generation time: 0.3 s, 0.8 s or 2.5 s.
   - **Next generation fails.**
   - **From an app: ask Rose for cooking.** Open an app first. The cooking page prepares in the
     background, the "Preparing… / Workspace ready · Show workspace" card appears, and the app stays in
     front.
   - **Reset workspace (clear saved state).**
7. Reload the page, then swipe right from Home: the workspace is exactly as you left it.

## Behaviour contracts

- **Fixed geometry.** The field, mic, Arrange/Regenerate/Undo, the status line (20 px), the recent row
  (28 px) and the canvas never move during loading, failure, undo or a change of content.
- **Loading.**
  - The status line reads "Composing on this phone… simulated".
  - A skeleton covers the old cards, which stay dimmed underneath.
  - The buttons are disabled in place.
  - A newer request supersedes an older one; a late result is ignored.
- **Failure.**
  - The status reads "Couldn't arrange · kept your last layout · Retry".
  - The layout and pins are untouched.
- **Gestures.**
  - A horizontal drag on the page flips pages. Going further right than the workspace resists.
  - A drag that starts on a carousel, the request field or the sheet belongs to that element.
    Carousels use `touch-action: pan-x` and scroll snap.
  - Vertical scrolling stays inside the canvas and its lists.
- **Voice.** The chin mic is the only mute. While muted, the workspace mic refuses to listen, and both
  a toast and its label say why.
- **Retention.**
  - The layout, pins, undo history and recent layouts survive opening apps, coming back and a full
    reload (r2).
  - The page never rearranges itself while you are away, unless you asked from a conversation.
- **Accessibility.**
  - Every control has a hit area of at least 44 pt.
  - The workspace is inert while off screen.
  - The status uses `role=status`, and the sheet is a modal dialog.

## Compositions (sample)

| Request | Cards |
| --- | --- |
| Cooking ("dinner", "cook", "recipe"…) | Kitchen apps · timer/music + grocery Rose task · recipe carousel · ingredient checklist · message from Jamie with quick reply |
| Work ("work", "focus", "meeting"…) | Next meeting (open invite + prep Rose task) · work apps · focus + draft-reply Rose task · today list · documents carousel |
| Unwinding ("unwind", "relax", "quiet"…) | Listening carousel · leisure apps · DND/dim + Saturday Rose task · reading list |
| Best guess (Arrange, or no matching request) | Fixed sample "Thursday 6:40 pm": Jamie's message, kitchen quick actions, listening, recipes, today |

## Evidence

`workspace-check.cjs` uses real input:

- Mouse at the 384×832 design size.
- CDP touch on iPhone 13 (390 px), iPhone SE (320 px) and a 390×600 short screen.

It runs Glacier at all four sizes and Oxblood at the design size and 390 px, then checks that Classic
has no workspace page. Bound runs are kept in `evidence/workspace-<commit>/`, with the JSON, the
screenshots and a SHA-256 manifest.

The same commit passes the existing suites: `verify.cjs`, `browser-check` (Oxblood, Classic, tray 3),
`phone-check` (Oxblood and Glacier) and `glacier-check`.

**Not covered:**

- A physical phone, Safari or a native build.
- Real model output, latency or failures.
- Review of the motion by a person, and screen-reader runs.

## Open for Designer and Nathan

- **Workspace mark (r2).** Is the star the right glyph and weight beside Home's dots?
- **Card vocabulary.** The cards are apps, actions, carousel, checklist and notification. Is this the
  set for native, and is the outline-plus-pearl treatment of Rose tasks the right distinction?
- **Request wording.** The status line echoes the request in quotes.
- **Offer card (r2).** It stays until it is acted on or dismissed. Should it time out?
- **Persisting Rose tasks.** Should Rose tasks also survive a restart in native? That is outside this
  prototype.
