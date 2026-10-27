# Automatic X bookmark research

The user enabled this on 2026-10-17 and authorized texting completed research
to their own verified Messages conversation. It is an event-driven feature of
Rose Agent, not a scheduled Codex chat and not a phone-driving agent.

## Flow

1. Android accessibility observes X's actual added-bookmark notification plus
   the changed bookmark control. Existing filled icons alone do not trigger it.
2. The phone captures the containing post card, control bounds and one frame.
   It sends these using the existing pinned TLS/device identity. The frame does
   not replace the foreground agent's gesture target. No taps, scrolling,
   navigation, speech, or repeated screenshots are performed by this feature.
3. An Android-private outbox retries network delivery using the same event ID.
4. The Mac durably queues captures under `~/.config/pixel-voice/bookmark-research`.
   Duplicate event IDs and matching post-content fingerprints are ignored.
5. A separate web-only model request researches full posts, threads, linked
   material and primary evidence. It has no shell, phone or messaging tools.
   It does not consume or steer the user's live voice/Codex conversation.
6. The Mac saves the full report and sends it to the preconfigured self chat
   through the existing Messages bridge. Recipient identity is checked first.
   Sending is journaled before execution; a timeout never blindly resends.
   Completion requires a new matching message with a sent/delivered flag.

The initial researcher is GPT-6 Luna, medium reasoning, with hosted web search
and a bounded eight-minute request. `store:false` preserves the existing API
storage setting. As with existing voice/research, model inputs go to OpenAI;
this is Mac-driven, not offline model inference. Captures/reports stay in private
local files as well. Nothing is posted publicly or sent to another contact.

## Controls and checks

- Authenticated `GET /bookmarks/config` supplies the enabled flag to the phone.
- Authenticated `POST /bookmarks/config` with `{"enabled":false}` pauses new
  captures; `true` re-enables them. It cannot alter the text recipient.
- Authenticated `GET /bookmarks/status` returns bounded job status, not screenshots.
- Authenticated `POST /bookmarks/capture` validates and queues a capture.
- The phone refreshes this flag every 30 seconds. Do not remove
  existing pairing credentials or alter the user's voice/model settings.
- Run `node --test rose-agent/bookmark-research/service.test.mjs`.

## Honest limits

X does not put a canonical post URL in the observed bookmark event. The Mac
resolves it from the captured author/content and web evidence. If it cannot
verify an exact URL or access a thread, the report must say so rather than
inventing a match or taking over the phone. Capture depends on X's current
English accessibility labels and coalesces a 700ms notification burst. Very
rapid saves, changed layouts, protected posts and stale/missing web indexes
need further coverage. The observed normal timeline flow is the initial target.
Research failures and uncertain deliveries remain visible in job status.

Restarted research can safely resume; an interrupted external send remains
uncertain for manual verification. The queue processes one research job at a
time, caps pending jobs at 25, and does not install content mentioned in posts.

## Verified deployment, 2026-10-17

Rose Agent 231 captured a real physical timeline bookmark of AICodeKing's
LeanREA post. The receipt contained the correct author and post text. The Pixel
was immediately returned to its launcher. The Mac's independent worker made
five web-search actions, consulted 31 source URLs, recovered the canonical post
URL, and returned a report that compared the benchmark's accuracy, speed and
cost claims against the repository. It explicitly disclosed that X itself
returned 403 and the replies were unavailable. No claim of reading inaccessible
thread contents was made.

The captured event completed research and a verified send in 22.205 seconds.
The existing Mac Messages database subsequently confirmed `sent=1` and
`delivered=1`. The live voice backend remained idle/ready, its prior Sol/Medium/
Ultrafast selection was preserved, and the phone remained on its launcher.
Nine targeted queue/delivery tests passed. An unauthenticated capture POST was
rejected. This verifies one actual automatic capture-to-text path; it does not
establish universal X layout coverage or guaranteed access to every thread.
