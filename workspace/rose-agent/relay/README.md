# Private Rose Agent relay

Own application code; no VPN app required. A public HTTPS relay receives authenticated phone requests and holds them for the Mac's outbound poll. The Mac forwards only explicitly allowed Rose Agent API routes to the existing locally pinned HTTPS service, then returns the response. WebRTC audio continues directly between the Pixel and OpenAI.

`relay.mjs` uses only Node's built-in modules. Deploy behind HTTPS, with exactly one instance (its short-lived request queue is in memory). Set distinct random PHONE_TOKEN and MAC_TOKEN secrets. Never expose these in source, logs, or URLs. The relay cannot choose arbitrary local destinations. Restarts fail pending requests rather than replaying potentially mutating work.

`mac-bridge.mjs` reads URL and Mac token from `~/.config/pixel-voice/relay.json`; the phone token goes only in the phone's private configuration. Cloud hosting and the phone relay transport are not deployed yet. This relay carries the existing voice/task API; APK delivery and remote device operations require a separate authenticated mechanism and are not implemented here yet.

This is a prototype transport, not a production multiparty service. It requires a reachable HTTPS deployment and the Mac to stay online.
