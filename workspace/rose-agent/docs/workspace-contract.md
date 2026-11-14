# Workspace page: OS Product's side of the contract

Project 2 adds one page to the left of the normal Home screen. You reach it by swiping right from Home.
It shows a locally composed layout of native sections. Home's own layout, its dock (the first cells of the
Home grid), the `HomeTray` edge and the page dots are unchanged.

**Status.** Nathan approved the HiFi prototype r2 on 2026-11-14 (`6fcf8c9b`,
`prototypes/rose-hifi/HANDOFF-WORKSPACE.md` on `claude/intelligent-ritchie-x2aqz4`). That handoff is the
behaviour reference. This document is source only: nothing here has been built for or run on a phone.

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
| actions: `launch` | `launch:<package>/<activity>` | the app's launch intent, via `WorkspaceLauncher.open` | yes |
| actions: `shortcut` | `sc:<package>/<shortcut id>` | `LauncherApps.getShortcuts` (Home holds the HOME role; no new permission) | yes |
| actions: `deeplink` | `intent:<template>` | intent templates Rose already has (alarm, timer, directions, music; `LocalRosePhone`) | yes |
| actions: `agent_task` | `task:<template id>` | the phone's task templates: `label` ≤ 60, `ask` ≤ 200, `goal` ≤ 300. The goal never enters the model. | after App Core's route |
| actions: `notification_action` | `nact:<key>/<index>` | notification actions | after D1 |
| actions: `app_function` | — | needs a privileged permission, so not inferred | no |
| notifications | `n:<key>` | see D1 | after D1 |
| content | `c:<source>/<id>` | no source yet (recipes, documents and media in the HiFi are sample data) | no |

Whether an action runs at once or asks first comes from its registry kind. Only `agent_task` asks first
("Rose task · asks first"). The composer cannot change that.

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

These pieces are checked by `app/tests/WorkspaceTest.java`, which `scripts/test-local.sh` runs.

## Answers to rose-layout-v1's other open questions

- **3. Pinned.** The phone places pinned sections itself, at their index. Their item ids are listed in
  `pinned` so the composer can avoid repeating them. They are preferred to be absent, not required, and
  the phone drops a repeat anyway.
- **4. Where the composer runs.** In a separate process of Rose Agent (`:composer`), which Home binds to.
  Model memory and a crash or out-of-memory kill then cannot take Home down. A dead process is
  `model_unavailable`.
- **6. On screen.** The composer is never told. The provider alone decides `shown` versus `prepared`.
- **7. Regenerate.** A fresh snapshot, with the previous layout's item ids as a soft avoid hint, as App
  Core proposed.

## Provider for App Core (§4.1)

The composer provider is Rose Agent itself, `dev.pixel.voice`. Its operations are `workspace_compose`
(kinds `prompt`, `arrange`, `regenerate` and `undo`) and `capabilities`. They are served the same way as
the customization tools. `capabilities` reports kinds only once the native page and a composer are on the
phone, so the conversation tool is not offered before then.

## Open decisions

| # | Decision | Status |
| --- | --- | --- |
| D1 | How Home gets notifications and their actions: (a) a notification listener in Rose Agent, which needs the person's notification-access grant; (b) the SystemUI Rose shade offers a bounded, signature-protected snapshot and action call, an OS Core change with security review; or (c) no notification cards at first. | Not decided. No permission is inferred. Until it is decided, the snapshot has no notifications. |
| D2 | Where voice requests are transcribed. A transcript reaches the composer as `direct_text`. | Not decided |
| D3 | The hook in Home's shared files (`MainActivity.attachLauncherPages`, `HomePages`, the page dots) that adds the page before Home. | To be agreed with Home/App Product |
