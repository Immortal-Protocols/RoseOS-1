# rose-layout-v1: the phone-local composer contract

Status: **Models Core's contract (LOCAL-COMPOSER-001/002), answered by OS Product and App Core in drafts (below).** OS Product owns the component catalog,
the context snapshot, the registry of real app/content/action IDs, the renderer, persistence, pin/undo and the
last-usable layout. App Core owns conversational routing. Models owns only what sits between: context selection,
the constrained model call and validation of its output. OS Product's phone re-checks every result before
rendering (`WorkspaceLayout.check`), so a composer bug can never break Home.

Confirmed: App Core accepted the mapping to its outer contract (`docs/APP-BUILDER.md` §4.4, PR 782 at
`41891f4c`), and OS Product adopted this contract as the phone's only layout format, supplied catalog `cat-1` and
answered the remaining questions (`workspace/rose-agent/docs/workspace-contract.md`, draft PR 787 at `d71fd72a`).
Nathan approved the Project 2 HiFi direction (r2, `prototypes/rose-hifi`, source `6fcf8c9b`); OS Product owns its
native integration.

## Boundary

```
OS Product  ──snapshot + catalog──▶  composer (on the phone, offline)  ──validated layout | typed failure──▶  OS Product renderer
App Core    ──request (trigger, prompt)──▶  composer
```

- Button ("Arrange for me" / best guess) and direct text run on the phone with no server. The server never
  generates a layout. A conversational request may *deliver* a request to the phone; the phone composes it.
- The model returns data, never code: a layout that references only IDs present in this request's snapshot, in
  components that exist in this catalog version. It cannot create IDs, intents, URLs or capabilities.
- Voice transcription (local or server) is a separate decision and is not part of this contract: a transcript
  arrives here as `direct_text`.

## Request

```json
{
  "contract": "rose-layout-v1",
  "requestId": "r-20261113-0001",
  "trigger": "button | direct_text | conversational",
  "prompt": "something for cooking dinner",
  "catalogVersion": "cat-1",
  "snapshot": { "...": "see Snapshot" },
  "limits": { "maxSections": 6, "maxItemsPerSection": 8, "maxTotalItems": 20, "deadlineMs": 4000 },
  "avoid": ["<the previous layout's item ids, on regenerate>"]
}
```

- `prompt` is null for `button`, at most 500 characters otherwise.
- `limits` may only tighten the catalog's own bounds.
- `avoid` (optional, at most 64 non-empty strings) is regenerate's soft hint. Ids no longer in the snapshot are
  ignored.

## Snapshot (built by OS Product)

A compact, versioned picture of what the page may show. Every `id` is opaque to Models and must be one the
registry can resolve and launch. `snapshotVersion` changes whenever any entry changes.

```json
{
  "snapshotVersion": "s-7f3a",
  "catalogVersion": "cat-1",
  "createdAtMs": 1794600000000,
  "timeOfDay": "morning | afternoon | evening | night",
  "apps":          [{"id": "<app id>", "label": "Calculator", "category": "tools", "recentRank": 3}],
  "notifications": [{"id": "<notification id>", "appId": "<app id>", "title": "Pasta timer done", "recentRank": 1}],
  "content":       [{"id": "<content id>", "kind": "media | contact | document | place | other", "label": "...", "appId": "<app id>"}],
  "actions":       [{"id": "<action id>", "label": "Start timer",
                     "kind": "launch | shortcut | deeplink | notification_action | app_function | agent_task",
                     "targetId": "<app/notification/content id>", "requiresConfirmation": false}],
  "pinned": ["<item ids of the sections the phone keeps pinned>"]
}
```

- Labels and titles are display text the OS has already chosen to show, truncated to 60 characters. Nothing else
  (no message bodies, no account names) enters the model.
- Hidden, sensitive or permission-gated items are left out by OS Product, not filtered by Models.
- Optional `category`, `kind` and `appId` are strings; `recentRank` is an integer from 0 to 1,000,000 (lower is
  more recent); `timeOfDay` is one of the four values or absent; `pinned` is a list of ids in the snapshot.
  Anything else is `bad_request`.
- `agent_task` is a Rose task (App Core PR 782 §4.3): it always asks first, so `requiresConfirmation` must be
  `true`. Its `ask` (confirm-sheet text) and `goal` (what Rose runs) may ride along in the snapshot but **never
  enter the model**: the composer reads only `id`, `label`/`title`, `category`, `kind`, `appId`, `targetId`,
  `recentRank` and `pinned`. It may be placed only as a `button` item or a `card` action, never in a `grid`.

## Catalog `cat-1` (OS Product's, from the approved HiFi r2 cards)

| component | items it may reference | items | notes |
|---|---|---|---|
| `grid` | apps, actions (not `agent_task`) | 2–8 | icon grid, 2–4 columns chosen by the renderer |
| `carousel` | content, notifications, apps | 2–8 | horizontally scrollable; must not fight the page swipe (renderer's job) |
| `list` | notifications, content | 1–8 | vertical, scrollable |
| `card` | one notification or content item, plus up to 2 actions that target it | 1 | |
| `button` | actions, including `agent_task` | 1–4 | a row of actions (HiFi "Quick actions"); `requiresConfirmation` actions still confirm in the OS |

Each section also has a `title` of at most 30 characters, plain text. The layout has a `title` of at most 40.

At most 6 sections and 20 items per layout; every id at most once. HiFi cards map as apps → `grid`, actions →
`button`, carousel → `carousel`, list/checklist → `list` (ticks are phone-local), notification → `card`. The
composer reads every rule from the catalog data in Python and Java alike, so a later catalog version needs no code
change.

## Layout (what the model may produce; the validator enforces all of it)

```json
{
  "title": "Dinner",
  "sections": [
    {"component": "grid",     "title": "Kitchen",    "items": ["a2", "a5", "x1"]},
    {"component": "card",     "title": "Timer",      "items": ["n1"], "actions": ["x3"]},
    {"component": "carousel", "title": "Recipes",    "items": ["c1", "c2", "c4"]}
  ]
}
```

- The model sees and writes short aliases (`a`=app, `n`=notification, `c`=content, `x`=action, plus an index),
  never raw IDs. The composer maps them back to the snapshot's real IDs, so the model cannot emit an ID it was not
  given, and tokens stay few.
- Decoding is grammar-constrained per request: the grammar enumerates only the aliases selected for this request
  and only the catalog's components, so a syntactically invalid layout cannot be produced. The validator still
  checks everything again (a runtime without grammar support, or a bug, must never break Home).

## Result

```json
{"contract": "rose-layout-v1", "requestId": "r-...", "status": "ok",
 "layout": {"snapshotVersion": "s-7f3a", "catalogVersion": "cat-1", "title": "Dinner",
            "sections": [{"component": "grid", "title": "Kitchen", "items": ["<real id>", "..."], "actions": []}]},
 "model": {"id": "...", "revision": "...", "quantization": "...", "runtime": "..."},
 "timing": {"contextMs": 3, "generationMs": 640, "validationMs": 1, "composeMs": 650}}
```

The composer may add diagnostic fields (`context`: entries, characters and output-token cap; `runtimeStats`:
whatever the runtime reports). They are for benchmarks and logs; the renderer ignores them.

or

```json
{"contract": "rose-layout-v1", "requestId": "r-...", "status": "failed", "failure": "<category>"}
```

Failure categories (fixed tokens; never model text):

| failure | meaning | OS Product does |
|---|---|---|
| `bad_request` | request outside the contract (unknown trigger, prompt too long, limits looser than catalog) | keep last layout |
| `catalog_mismatch` | the snapshot and request name different catalog versions | keep last layout |
| `stale_snapshot` | a layout names a snapshot version other than the current one | discard it, keep last |
| `empty_context` | nothing selectable to compose from | keep last layout |
| `model_unavailable` | the model or runtime is not loaded or failed to load | keep last layout |
| `timeout` | no output within `deadlineMs` | keep last layout |
| `cancelled` | the caller cancelled | nothing |
| `invalid_output` | not one JSON object of the layout shape | keep last layout |
| `unknown_ref` | an alias not given in this request | keep last layout |
| `disallowed_ref` | a reference the component does not allow (e.g. an app in a `list`, an action not targeting the card's item) | keep last layout |
| `over_limit` | too many sections or items, or a title too long | keep last layout |
| `duplicate_ref` | the same item twice in one layout | keep last layout |

`timing.composeMs` is composer-side only. **Request-to-render latency needs the renderer's completion timestamp
from OS Product**; until it exists, results are labelled generation-only.

## Relation to App Core's composer intent/result v1

App Core's contract (`docs/APP-BUILDER.md` §4, proposed in PR 782) is the outer hop: the server sends a
`workspace_compose` command to OS Product's phone composer provider, and the provider reports
`shown | prepared | rejected | unavailable | uncertain`. This document is the inner, on-phone call from that
provider to the Models composer. The two do not overlap; the provider translates:

| App Core `kind` (server → phone) | this contract |
|---|---|
| `prompt` (owner's words, ≤ 500 chars) | `trigger: conversational`, same `prompt`; typed text on the page is `direct_text` |
| `arrange` (best guess, no text) | `trigger: button`, `prompt: null` |
| `regenerate` | the provider recomposes the last request; deterministic decoding returns the same layout, so a variation input is an open question (7) |
| `undo` | OS Product only (last-usable layout); no composer call |

| this result | App Core status the provider reports |
|---|---|
| `ok`, then rendered | `shown` (or `prepared` if saved off screen) |
| `failed: model_unavailable` | `unavailable` |
| any other `failed` | `rejected`, with the category as `reason` |
| `cancelled` or no answer | as the provider knows it (`rejected` or `uncertain`) |

`requestId` (≤ 64 chars) passes through unchanged, so App Core's repeat-returns-first-outcome rule holds at the
provider.

## Open questions: all answered

1. ~~Components and bounds.~~ OS Product's `cat-1` (above), from the HiFi r2 cards.
2. ~~ID format and first-build kinds.~~ Opaque, stable ids in OS Product's registry formats (`app:`, `launch:`,
   `sc:`, `intent:`, `task:`; `n:`/`nact:` after its decision D1). The first build has apps and actions only; no
   notifications until D1 is decided and no content source yet.
3. ~~Pinned.~~ The phone places pinned sections itself, at their index. `pinned` lists their item ids so the
   composer does not repeat them: the composer leaves them out of the context entirely (preferred absent, not
   required; the phone drops a repeat anyway).
4. ~~Where it runs.~~ A separate `:composer` process of Rose Agent that Home binds to; a dead process is
   `model_unavailable`.
5. ~~Conversational fields.~~ Only `trigger`, `prompt` and `requestId` (App Core).
6. ~~On screen.~~ The composer is never told; only the provider decides `shown` versus `prepared`.
7. ~~Regenerate.~~ A fresh snapshot plus the previous layout's item ids in the request's `avoid` list (App Core's
   proposal, OS Product's answer). Avoided ids rank after every other entry, so the context, and therefore the
   deterministic layout, changes; they stay selectable if nothing else fits.

Sources: OS Product `workspace/rose-agent/docs/workspace-contract.md` (draft PR 787 at `d71fd72a`); App Core
`docs/APP-BUILDER.md` §4.4 (PR 782 at `41891f4c`). Both are drafts; if either changes, this document and
`contract/vectors/v1.json` change with it.
