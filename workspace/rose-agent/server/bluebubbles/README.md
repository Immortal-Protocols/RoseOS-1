# Complete Messages operations

The Pixel launcher and agent call the official BlueBubbles app **Messages**.
Its package/signature, stored conversations, and official updates are unchanged.

Four registered operations are exposed: find chats, open a specific chat on the
Pixel, read recent messages, and send authorized text to an existing chat. They
use the installed BlueBubbles Server's authenticated HTTPS REST interface. Only
opening a chat needs phone interaction: the official Android app's `chatGuid`
intent (also used by its notifications) selects it, then the phone's actual
foreground and conversation header/composer are checked. Merely launching the
app is not completion. Drafts are not populated or cleared.

## Why this route

The official Flutter UI exposes several unlabeled icon controls. Mapping every
one into the agent would preserve the slow navigation and ambiguous taps the
user asked us to avoid. The existing Mac API gives authoritative conversation
identities, message direction/time, and outgoing records without navigating the
Pixel. No private API, SIP change, library-validation change, relay, or new
account is required. Source was checked against the installed Android release
v2.1.2+92 and Server v1.9.9 in `experiments/bluebubbles-trial`.

API references:
- https://docs.bluebubbles.app/server/developer-guides/rest-api-and-webhooks
- https://docs.bluebubbles.app/client/usage-guides/tasker-integration

## Correctness boundaries

- Chat queries page through the complete server index; paging is by ID, not
  recency. Only identities are cached for two minutes. Messages are read fresh.
- Names match Mac contact addresses. An ambiguous recipient is returned for
  clarification, with addresses and recent activity; it is never guessed.
- Read results preserve sender/direction, Unix milliseconds and ISO timestamps,
  attachment metadata, and truncation/paging limits. They do not mark read.
- Sending requires explicit user intent and a stable operation ID. An on-disk
  record is written **before** dispatch. A duplicate ID returns the previous
  result; the same ID with different text/recipient is rejected. Uncertain
  results are not retried automatically, even after a server restart.
- Send success requires an error-free outgoing record and a separate readback
  of the exact text in the selected chat. Delivery is reported independently.
- Only normal text into existing chats is implemented. Creating chats,
  attachments, reactions, edit/unsend, typing, read receipts and advanced
  features are not advertised by these tools.
- The existing Mac server must be reachable. Failure does not silently switch
  to another recipient or a UI send path.

## Credential handling

The existing password is read from the installed server configuration database,
used in memory for the loopback HTTPS request, and never returned to the model,
placed in command arguments, or copied into an app/APK. TLS verifies the server
certificate against the already configured public address. Send journals are
private local files; they contain message contents and recipients for reliable
recovery and must not be published.

## Checks

`node --test rose-agent/server/bluebubbles/adapter.test.mjs` covers the common
catalog path, paging, ambiguity, wrong-chat verification, timestamp/direction,
concurrent/restarted retries and uncertain sends. Live validation must also use
the normal Rose Agent provider and worker, measure end-to-end time, and inspect
the Pixel's resulting chat. An API call alone is not a user-experience speed test.
