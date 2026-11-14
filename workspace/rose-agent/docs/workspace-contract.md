# Workspace page: OS Product's side of the contract

Project 2 adds one page to the left of the normal Home screen. You reach it by swiping right from Home.
It shows a locally composed layout of native sections. Home's own layout, its dock (the first cells of the
Home grid), the `HomeTray` edge and the page dots are unchanged.

**Status.** Nathan approved the HiFi prototype r2 on 2026-11-14. Its source is `6fcf8c9b`, and it reached
main in `5baea381`. The behaviour reference is `prototypes/rose-hifi/HANDOFF-WORKSPACE.md` and
`prototypes/rose-hifi/src/workspace.js`. Both are byte-identical at `6fcf8c9b` and on main; main adds only
review evidence under `prototypes/rose-hifi/evidence/workspace-6fcf8c9b/`. The pixel review is not complete,
and its remaining observations stand. This document is source only: nothing here has been built for or run
on a phone.

## Three contracts, one each

| Hop | Contract | Owner |
| --- | --- | --- |
| Server → phone: a conversational request, and its result | composer intent/result v1, `docs/APP-BUILDER.md` §4 (#782) | App Core |
| Phone → on-phone composer → phone: snapshot in, layout or typed failure out | `rose-layout-v1`, `workspace/models/local-composer/contract/rose-layout-v1.md` (#781) | Models |
| The catalog, the snapshot, the real ids, the renderer, persistence, pins, undo and the last usable layout | this document | OS Product |

There is no separate OS Product layout format. The phone renders `rose-layout-v1` layouts, after its own
check.

## Catalog `cat-1` (OS Product's answer to rose-layout-v1 open question 1)

This catalog is drawn from the approved HiFi r2 cards.

| Component | HiFi card | Items it may reference | Items | Actions |
| --- | --- | --- | --- | --- |
| `grid` | apps | apps, actions (never `agent_task`) | 2–8 | 0 |
| `carousel` | carousel | content, notifications, apps | 2–8 | 0 |
| `list` | list / checklist | notifications, content | 1–8 | 0 |
| `card` | notification | one notification or content item | 1 | up to 2 that target that item |
| `button` | actions ("Quick actions") | actions, including `agent_task` | **1–4** (a row) | 0 |

These are the changes from Models' starting set:
- **`button` takes 1–4 actions, not 1.** The HiFi's action card is a row of 2–3 buttons.
- **At most 6 sections, not 4.** The HiFi's cooking and best-guess pages have 5.
- **`agent_task` is allowed only in a `button` row or as a `card` action.** App Core §4.3 asked for this.

These bounds stay as Models proposed:
- section titles ≤ 30 characters, layout title ≤ 40;
- ≤ 20 items in total;
- every id at most once per page.

Checklist ticks are local display state on the phone and never reach the composer.

## Registry and ids (open question 2)

Ids are opaque to Models and App Core. They are stable while the thing they name exists, so pins and saved
layouts survive a new snapshot.

| Kind | Id format | Source | In the first build |
| --- | --- | --- | --- |
| apps | `app:<package>/<activity>` | `queryIntentActivities(MAIN/LAUNCHER)`, Home's own component key | yes |
| actions: `launch` | `launch:<package>/<activity>` | reserved for content that opens in an app. An app entry already opens the app, so there is no launch copy of each app. | no |
| actions: `shortcut` | `sc:<package>/<shortcut id>` | `LauncherApps.getShortcuts` (Home holds the HOME role; no new permission) | yes |
| actions: `deeplink` | `intent:<template>` | intent templates Rose already has (alarm, timer, directions, music; `LocalRosePhone`) | yes |
| actions: `agent_task` | `task:<template id>` | the phone's task templates: `label` ≤ 60, `ask` ≤ 200, `goal` ≤ 300. The target is Rose Agent's own app entry, because every rose-layout-v1 action names one. The ask and goal never enter the model, and `requiresConfirmation` is always true. | after App Core's route |
| actions: `notification_action` | `nact:<key>/<index>` | notification actions | after D1 |
| actions: `app_function` | — | needs a privileged permission, so not inferred | no |
| notifications | `n:<key>` | see D1 | after D1 |
| content | `c:<source>/<id>` | no source yet (recipes, documents and media in the HiFi are sample data) | no |

A package, activity, shortcut or template name that is empty, longer than 200 characters, or contains a
space, `/`, `"` or `\` cannot make an unambiguous id, so it is left out.

Whether an action runs at once or asks first comes from its registry kind. Only `agent_task` asks first
("Rose task · asks first"). The composer cannot change that.

## The snapshot

`WorkspaceSnapshot.build` makes the rose-layout-v1 snapshot and its registry together, so the composer is
offered exactly the ids the phone can open.

- **Labels** are display text the OS already shows. Control characters become one space, invisible format
  characters are dropped, and the text is cut to 60 code points. An entry with no visible label is left out.
- **Bounds:** at most 120 apps and 60 actions, and the first entry of a repeated id wins.
- **Notifications and content** are empty until D1 and a content source exist.
- **Pins:** `pinned` keeps only ids that are present.
- **The version** is `s-` plus 12 hex digits of SHA-256 over every entry and the pins. `createdAtMs` and
  `timeOfDay` are not part of it, so a new hour alone never makes a layout stale.
- **Requests:** `WorkspaceSnapshot.request` writes the request with cat-1's own limits (6 sections, 8 per
  section, 20 in total). On regenerate it adds `avoid`: the previous layout's ids, unique and at most 64.

## The phone's own check

`WorkspaceLayout.check` re-checks every composer result before anything renders. It does this because a
runtime without grammar support, or a bug, must never break Home. It checks:
- the contract, `requestId` and catalog version;
- the snapshot version, which must equal the snapshot built for that request; otherwise the result is
  `stale_snapshot`;
- every section against `cat-1`;
- that every id is in the registry, of an allowed kind, and appears once;
- that card actions target the card's item;
- titles, which must be plain printable text.

Failures use rose-layout-v1's fixed tokens, never model text, and keep the last layout.

At render time, `WorkspaceLayout.available` leaves out any item whose app was removed or whose notification
went away since the layout was made. It also leaves out a section that falls below its minimum. The saved
layout is never reshuffled.

## On the phone

| Piece | What it does |
| --- | --- |
| `WorkspaceState` | Keeps the layout, the request it answered, pins, the last 20 undo steps, 3 recent layouts and the last result of each direct action. It survives restarts, and a corrupt store gives an empty page. Pinned sections keep their exact index when a new layout arrives, and only the unpinned ones around them change. Nothing rearranges by itself. |
| `WorkspaceComposer` | Handles one request at a time. It maps to rose-layout-v1 triggers: Arrange is `button`, typed text is `direct_text`, and a conversational request is `conversational` and keeps App Core's `requestId`. A newer request supersedes an older one, and a late result is ignored. A failure keeps the layout and pins. A conversational request raises the "Preparing workspace / Workspace ready · Show workspace" offer and never leaves the app. `routeStatus` gives App Core's `shown`, `prepared`, `rejected`, `unavailable` or `uncertain`. |

| `WorkspacePage` | The native page, at `HomePages.WORKSPACE` (0), left of Home (1). Its geometry is fixed: the field, Arrange, Regenerate and Undo, the status line (20dp), the recent row (28dp) and the canvas never move. Each section has a Pin control. While composing, the old sections stay dimmed under a veil and the buttons are disabled in place. Apps are drawn and opened exactly as Home draws and opens them. Shortcuts open through `LauncherApps`, and intent templates open their app. A drag that starts on the field or a carousel belongs to it, not to the pager, and the page is inert while off screen. Its state is saved in the app's own preferences after every change. |
| `WorkspaceSources` | Reads the phone into a snapshot on the page's worker thread: launchable apps without Rose Agent, launcher shortcuts (only while Rose Agent holds the HOME role, up to 2 per app), and the timer and alarms templates. |
| `WorkspaceEngine` | One composer call through Models' core (`workspace/models/local-composer/java`, `Catalog.cat1()`), now packaged in the app by `build.sh`; its scripted test double is not packaged. It never throws: anything unexpected is a rose-layout-v1 failure. |
| `WorkspaceStarter` | The only inference on the phone today, and **not a model**. It ignores the request's words. It answers with the apps it is offered as a grid, and up to 4 direct actions as a button row. It never offers a Rose task. The page labels it "Starter layout · no on-phone model yet". Models' runtime replaces it behind the same `LocalInference` seam. |
| `WorkspaceTiming` | Request-to-render timing, the renderer half of rose-layout-v1's timing. See below. |

These pieces are checked by `app/tests/WorkspaceTest.java`, `WorkspaceSnapshotTest.java`,
`WorkspaceEngineTest.java` and `HomePagesTest.java`, and by `tests/home-launcher.test.mjs`. `scripts/test-local.sh`
runs them all.

## Request-to-render timing (for Models)

rose-layout-v1 leaves the renderer's completion timestamp to OS Product. `WorkspaceTiming` keeps one record per
request (the last 20). Each record holds only ids, triggers and numbers, never the request's text.

| Point | Where it is taken |
| --- | --- |
| requested | `WorkspaceComposer.begin`, on the main thread |
| result | the composer's result reaching the main thread |
| rendered | the first draw pass after the committed layout (a one-shot `ViewTreeObserver.OnDrawListener`) |

All three are `System.nanoTime()`. A record gives these fields:

- `requestToResultMs`, `resultToRenderMs` and `requestToRenderMs`.
- The composer's own `timing`, copied as reported.
- `basis`: `request-to-render` once a frame showed the layout. A failed, superseded or undrawn request is
  `result-only`, and it never renders.
- `modelId`, and `model`: false for the starter and for `scripted`, so neither is ever reported as a model
  measurement.

`WorkspacePage.timingJson()` returns the records as JSON. Nothing sends them anywhere.

## Answers to rose-layout-v1's other open questions

- **3. Pinned.** The phone places pinned sections itself, at their index. Their item ids are listed in
  `pinned` so the composer can avoid repeating them. They are preferred to be absent, not required, and
  the phone drops a repeat anyway.
- **4. Where the composer runs.** In a separate process of Rose Agent (`:composer`), which Home binds to.
  Model memory and a crash or out-of-memory kill then cannot take Home down. A dead process is
  `model_unavailable`. That split comes with Models' runtime. Until then the composer's pure Java core and the
  starter run on the page's own worker thread, which loads no model.
- **6. On screen.** The composer is never told. The provider alone decides `shown` versus `prepared`.
- **7. Regenerate.** A fresh snapshot, with the previous layout's item ids as a soft avoid hint, as App
  Core proposed.

## Provider for App Core (§4.1)

The composer provider is Rose Agent itself, `dev.pixel.voice`. It serves two operations, the same way as the
customization tools: `workspace_compose`, with kinds `prompt`, `arrange`, `regenerate` and `undo`, and
`capabilities`.

**The route in.** `SettingsAgentProvider.call` checks the caller's signer, and its receipts answer a repeated
command id. It then hands `workspace_compose` to `WorkspaceHub.serve`.

**One owner.** `WorkspaceHub` owns the workspace for both the page and the provider: one saved state, one
composer, and one dispatcher (`WorkspaceRequests`).

**Requests** (`WorkspaceAgent.parse`) are read strictly:
- only `schemaVersion`, `requestId`, `kind` and `text`;
- `schemaVersion` must be 1, and `requestId` 1 to 64 characters;
- `text` must be present for `prompt` and only for `prompt`: not blank, at most 500 characters.

A bad request is `rejected` with a fixed reason. A `requestId` already answered gets its first answer again,
and nothing is composed.

**Answers** follow §4.2: `{schemaVersion: 1, requestId, status, revision?, reason?}`.

| Case | Answer |
| --- | --- |
| a composition commits while the workspace is on screen | `shown`, with the store's revision |
| a composition commits while the workspace is off screen | `prepared`, with the store's revision; it is saved, and the page shows it when reached |
| `model_unavailable` | `unavailable` |
| any other rose-layout-v1 failure | `rejected`, with the failure token |
| superseded by a newer request | `uncertain` |
| still composing after 7 s (the provider waits 8 s) | `uncertain`; the composition still commits, and a repeat of the `requestId` gets that answer |
| `undo` | answers at once, with no composer; refused while composing, and "nothing to undo" is `rejected` |

**The revision** grows with every change to the layout or its pins, undo included. The highest revision saved
is kept apart from the store, so it never goes back.

**`capabilities`** adds `kinds` (§4.1). `prompt` is listed only with a real model. The starter ignores words,
so today the kinds are `arrange`, `regenerate` and `undo`.

**Not here yet: D4, the offer outside Home.** "Preparing workspace / Workspace ready · Show workspace"
outside Home needs a surface, either a notification or an overlay, and both raise permission questions. Until
that is decided, a conversational request reads `prepared`, and the page shows it when reached.

## Rose tasks (App Core's route, #782)

A confirmed `agent_task` tap sends a signed `POST /workspace/v1/tasks`. The route exists in source, and it is
mounted only when the server enables it, which nothing does yet. The phone treats the answers as follows:

| Answer | Meaning | Phone |
| --- | --- | --- |
| 200 `accepted`, `taskId` | started | "Started". `taskId` may be null until App Core's conversation wiring exists, so card state never depends on it. |
| 200 `duplicate`, first `taskId` | the same device and `requestId` again; nothing new started | Same as the first answer. A retry reuses the tap's `requestId`. |
| 400 `refused`, `reason` | bad body, an id that isn't `task:<id>`, label > 60, goal > 300, or no `confirmed: true` | "Couldn't start". Not retried. |
| 503 `unavailable` | nothing started | "Couldn't start · try again". Safe to retry with the same `requestId`. |
| 401 | signature failure | Same as the builder routes. Not retried. |

## Open decisions

| # | Decision | Status |
| --- | --- | --- |
| D1 | How Home gets notifications and their actions: (a) a notification listener in Rose Agent, which needs the person's notification-access grant; (b) the SystemUI Rose shade offers a bounded, signature-protected snapshot and action call, an OS Core change with security review; or (c) no notification cards at first. | Not decided. No permission is inferred. Until it is decided, the snapshot has no notifications. |
| D2 | Where voice requests are transcribed. A transcript reaches the composer as `direct_text`. | Not decided |
| D3 | The hook in Home's shared files that adds the page before Home: `MainActivity.attachLauncherPages`, `HomePages`, and the page dots with the workspace's star. | Agreed with Home/App Product and in source: `WORKSPACE=0`, `HOME=1`, `FIRST_APPS=2`. HOME on Home goes to Home, Back goes to Home, a stale page restores to Home, and the dots match the pages. |
| D4 | Where the "Workspace ready · Show workspace" offer appears while the person is in another app: a notification, an overlay, or nothing (the page shows it when reached). | Not decided. No permission is inferred. Today: nothing outside Home. |
