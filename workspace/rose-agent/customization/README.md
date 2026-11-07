# Rose customization CLI

Nov 7 plan 1O. Rose's four customization controls change live, with no OS
rebuild. Rose Agent dispatches them, Home applies them and Rose's conversation
registers them in this source (#626, #627). On a phone they still need an
installed Agent build and a Rose backend restarted from main (limitation e).
Host tests here are not device evidence:

| Control | Tool | Values |
| --- | --- | --- |
| Home column count | `customization_home_columns` | `columns`: 0 (automatic) or 3 to 8 |
| Oxblood wallpaper variant | `customization_wallpaper` | `variant`: classic, garnet, ember, velvet, dusk (all Oxblood) |
| Bundled font | `customization_font` | `font`: default, nunito, sans, serif, condensed, mono |
| Glasses-on hook | `customization_glasses_hook` | `enabled`, `action` tray or app, `packageName` |

`customization_state` reads all four.

## One catalog, one command

Both paths use `../app/assets/customization-agent-tools.json`:

- **Conversation.** The shared Rose backend (`prototypes/voice-phone-demo`,
  the same code on the Mac and in the cloud) gets the tools from
  `rose/customization.py`: `TOOLS` is the catalog unchanged, and `call()` sends
  the command over the existing phone link. App Core registers them in
  `rose/codex_conductor.py` (App Core's file, #627). The older Node
  server can load the same catalog as `dev.pixel.voice` app tools
  (`server/app-tool-catalog.mjs`).
- **This CLI.** `controls.mjs` loads the same file. It validates arguments with
  the same checks and error messages as the server's `execute()` and builds the
  same command. With `--say` it does not send that command itself. It submits
  the change as a typed request to Rose's conversation (`POST /api/say` on the
  Mac dashboard, loopback only), so the change runs through the same tool:

```json
{"action":"app_command","packageName":"dev.pixel.voice","operation":"customization_home_columns","params":{"columns":5}}
```

`rose-customize.test.mjs` feeds the catalog through the real Node server
loader. It checks that a conversation tool call and the CLI produce deep-equal
commands, and that both validators accept or reject the same argument table with
the same messages. It also checks that the Python module builds the identical
command, and exercises `--say` against a local stand-in for `/api/say`.

## Usage

```sh
node rose-customize.mjs list                   # controls and allowed values, read from the catalog
node rose-customize.mjs columns auto           # dry run (default): prints the app_command
node rose-customize.mjs columns 6
node rose-customize.mjs wallpaper velvet
node rose-customize.mjs font serif
node rose-customize.mjs glasses-hook --on --tray
node rose-customize.mjs glasses-hook --app com.example.notes
node rose-customize.mjs --say columns 5        # ask Rose (this Mac, port 8091) to make the change
node rose-customize.mjs --say --port 8092 state
```

Exit codes:

- 0: printed (dry run), or Rose accepted the typed request (say). The phone's
  result is in Rose's reply; the CLI does not confirm it.
- 1: Rose not reachable on loopback, or not ready
- 2: invalid input (the validator's message goes to stderr; nothing is sent)

Test: `node --test workspace/rose-agent/customization/rose-customize.test.mjs`
(offline, and part of `scripts/test-local.sh`).

## Current limitations

a. **The CLI has no direct command route, by design.** No authorized route
   lets a local CLI send `app_command` as the paired phone. The Node server's
   `phoneCommandRejection` refuses mutating actions from pair-token callers,
   and adding a loopback command endpoint would be a new trust surface needing
   Chief/owner approval. `--say` therefore goes through Rose's conversation. It
   needs Rose running on this Mac with the customization tools registered, and
   only reports that Rose accepted the request.
b. **Node server only: the tools load only when configured.** `dev.pixel.voice` tools load only
   when the private app-tools config (`app-tools-trial.json`) lists the package
   and its catalog.
c. **Node server only: background-control mode filters them.** In that mode,
   `background-app-policy.mjs` drops any `dev.pixel.voice` operation that is not
   on its allowlist. None of the `customization_*` operations are on it yet.
d. **No glasses-worn signal.** The hook can be configured, but it never fires
   at this revision. The real source is Meta DAT `Device.donState`
   (DONNED/DOFFED). That lives in the external Glasses Camera app
   (`dev.pixel.glassescamera`), not in this repo. Relaying it needs a
   coordinated change outside this repo and Meta registration. A Bluetooth
   connection and the hinge/unfold advertisement are never treated as worn.
e. **Not deployed.** The conversation tools are registered in source (#627),
   but `--say` reaches them only when Rose on this Mac runs from main and the
   phone has an Agent build with this source installed.
f. **Some glasses-hook rules are enforced only on the phone.** The server's
   validator (and this CLI's mirror of it) accepts these, and the phone's
   `CustomizationConfig` refuses them, which is the intended authority:
   `{}`; `packageName` alone while the stored action is tray; `action: tray`
   with a non-empty `packageName`; `action: app` with no package given or
   stored; and a `packageName` that is not a dotted Java package name. The
   CLI rejects a malformed `--app <package>` itself (exit 2) and requires at
   least one option. Making the server refuse `{}` (for example a
   `minProperties` rule) is a change for the server's owner.
