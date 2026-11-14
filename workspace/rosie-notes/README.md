# Rosie notebook

Plan 1F source-only private notebook and ordinary background-workspace surface.
Package `dev.pixel.rosienotes`; not the owner's Notes app or Rose Agent Home.

Read [the private-note contract](../../docs/PRIVATE-LIFE-NOTES.md) before changes.
The app has no permission requests or network access. Its read-only launcher
Activity exposes only its own notes. Its provider requires the existing app
signer and independently verifies the real controller session before new writes.

Offline synthetic checks: `bash scripts/test-private-life-notes.sh` from the repo
root. `build.sh` is an unsigned local recipe, not an automatic build, signing,
installation or publication step. No native build or phone acceptance is claimed.
