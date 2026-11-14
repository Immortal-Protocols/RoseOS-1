# Workspace page contract (`workspace-v1`)

Project 2 adds one page to the left of the normal Home screen. You reach it by swiping right from Home.
It shows a locally composed layout of native cards. Home's own layout, its dock (the first cells of the
Home grid), the `HomeTray` edge and the page dots are unchanged.

**Status.** Nathan approved the HiFi prototype r2 on 2026-11-14: `6fcf8c9b`,
`prototypes/rose-hifi/HANDOFF-WORKSPACE.md` on `claude/intelligent-ritchie-x2aqz4`. That handoff is the
behaviour reference. This document is the native contract. It is source only: nothing here has been
built for or run on a phone.

**Owners.**

| Owner | Responsible for |
| --- | --- |
| OS Product | The page, the context snapshot, the id registry, the validator, the native cards, gestures and persistence |
| Models | Model selection, benchmarking, context selection and on-device inference that produces the reply below |
| App Core | The conversational request/result route and Rose tasks |
| Designer | Visuals and motion |

## The rule

The composer composes; the phone checks and renders.

- **Ids only.** The composer names only short ids that the phone resolved before the request. It never
  supplies a package, component, Intent, URI, style or code.
- **The phone decides how an action runs.** Whether an action runs at once ("Direct") or asks Rose first
  ("Rose task · asks first") comes from the phone's registry. The composer cannot choose it.
- **Bad replies show nothing.** A reply that fails the check is never shown in part. The last layout and
  its pins stay.

This follows Models' `local-agent-v1` (`workspace/models/contracts/local-agent-v1.md`): opaque allowlist
ids, one strict JSON envelope, and only the platform's results record what happened.

## Reply (composer → phone)

The whole reply is exactly one JSON object.

```json
{"rose_layout": "workspace-v1",
 "title": "Cooking tonight",
 "cards": [
  {"type": "apps", "title": "Kitchen apps", "items": ["a2", "a5", "a7"]},
  {"type": "actions", "title": "Quick actions", "items": ["x9", "x3"]},
  {"type": "carousel", "title": "Listen", "items": ["a3", "x4"]},
  {"type": "list", "title": "Ingredients", "check": true, "items": ["Spaghetti 200 g", "Lemon"]},
  {"type": "notification", "title": "Jamie", "items": ["n4"]}]}
```

| Card | `items` | Bounds |
| --- | --- | --- |
| `apps` | app ids | 1–8 |
| `actions` | action ids | 1–4 |
| `carousel` | app or action ids | 2–10 |
| `list` | plain text; `"check": true` makes a checklist | 1–8 items, ≤ 60 characters each |
| `notification` | one notification id (its actions come from the notification itself) | 1 |

- **Page:** 1–8 cards. The `title` is at most 32 characters. Card titles are at most 40.
- **Text:** all text is plain. Any control character rejects the reply.
- **Ids:** they match `[a-z][a-z0-9.]{0,15}`, and each appears at most once per page.
- **Rejected whole:** prose or code fences around the object, a second object, a duplicate key, an unknown
  field or card type, a wrong `rose_layout`, any bound exceeded, or an id of the wrong kind (an app id in
  `actions`). The reason is recorded as `malformed`, `contract`, `unknown_field.<name>` or the failing
  path.
- **Stale, not invalid:** an id the phone no longer has (an app removed, a notification gone) is dropped.
  A card left empty is dropped, and so is a carousel left with fewer than 2 items. If no card remains, the
  reply is rejected as `nothing_available`.

The rules are in `WorkspaceLayout.parse`. They are checked by `app/tests/WorkspaceTest.java`, which
`scripts/test-local.sh` runs.

## Context snapshot (phone → composer): proposal for Models

This is not built yet. It is the proposal for Models to tune. It is assembled on the phone for each
request and never sent to the Rose server.

```json
{"contract": "rose-workspace-context-v1",
 "request": {"kind": "arrange" | "text", "text": "…≤500 chars…"},
 "now": {"local_time": "18:40", "day": "thu", "daypart": "evening"},
 "apps": [{"id": "a7", "label": "Messages", "group": "social"}],
 "actions": [{"id": "x3", "app": "a7", "label": "New message"}],
 "notifications": [{"id": "n4", "app": "a7", "title": "Jamie", "age": "5m"}],
 "pinned": [{"type": "apps", "title": "Kitchen apps"}]}
```

- **Proposed bounds:** 60 apps, 40 actions and 20 notifications. Notification text is cut to 80
  characters, and the whole snapshot is at most 8 KB.
- **Where ids come from:** the same component keys Home uses (`package/activity`) for apps;
  `LauncherApps` shortcuts (Home holds the HOME role), the intent templates Rose already has and suite
  `<pkg>.agent` operations for actions; and notifications once D1 below is decided.

## On the phone

| Piece | What it does |
| --- | --- |
| `WorkspaceState` | Keeps the layout, the request it answered, the pins, the last 20 undo steps, 3 recent layouts and the last result of each direct action. It survives restarts (`toJson`/`fromJson`). A corrupt store gives an empty page, never a crash. Pinned cards keep their exact index when a new layout arrives, and only the unpinned cards around them change. Nothing rearranges by itself. |
| `WorkspaceComposer` | Handles one request at a time. A newer request supersedes an older one, and a late reply is ignored. A failure keeps the last layout and pins. A request from inside an app never leaves the app: it raises the "Preparing workspace / Workspace ready · Show workspace" offer, which clears when the workspace is reached. |

## Entry points

| Entry | Path | Server |
| --- | --- | --- |
| Arrange for me | Local composer (best guess) | None |
| Direct text | Local composer | None |
| Voice | Composition is local. Transcription is decision D2. | Depends on D2 |
| Conversation with Rose | App Core: proposed `workspace_prepare {request}` over `/ws/phone` (`LocalRosePhone` → `PhoneControl`), answered by `{status, layout_revision}`. The phone still composes locally. Rose tasks are routed back through App Core when confirmed. | App Core's route |

## Open decisions

| # | Decision | Status |
| --- | --- | --- |
| D1 | How Home gets notifications and their actions: (a) a notification listener in Rose Agent, which needs the person's notification-access grant; (b) the SystemUI Rose shade offers a bounded, signature-protected snapshot and action call (an OS Core change with security review); or (c) no notification cards at first. | Not decided. No permission is inferred. Until it is decided, no notification ids are offered, so no notification cards appear. |
| D2 | Where voice requests are transcribed: a local model (Models), the existing server path (the Rose server is stopped until Nathan restarts it), or voice later. | Not decided |
| D3 | The hook in Home's shared files (`MainActivity.attachLauncherPages`, `HomePages`, the page dots) that adds the page before Home. These files are shared with Home/App Product. | To be agreed with Home/App Product before any edit |
