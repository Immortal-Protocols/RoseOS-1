# Rose system interaction prototype

Snapshot of Rose-Fold-v15, accepted by Nathan as functionally correct on October 29, 2026. This is an isolated browser experiment, not production app or OS code or visual approval.

Open `Rose-System-Interactions.html` in a browser that permits local HTML. The file contains editable HTML/CSS/JavaScript and embedded images; no installation, backend or microphone access is needed.

## Explore

- Home has paged app icons and a persistent Rose/system-switcher dock.
- The system switcher has a personal-app carousel and a smaller background-agent tray. Drag with a mouse or swipe; click empty space to dismiss.
- The Rose icon/animated activity connector links to the separate in-app Background work manager: four app windows per folded page, eight unfolded, with Start, Pause/Resume and confirmed Stop.
- Opening an empty system switcher loads two running example jobs and disables automatic completion for activity review. All app content is synthetic.
- Demo controls expose simulated voice, task events, audio opt-in, lock, reduced motion and reset. Mic, output, tasks and navigation have independent lifecycles.

## Verification

Run `node prototypes/rose-system-interactions/verify.cjs` from the repository root. The 33 source/model checks exercise scripts, state transitions, task persistence and distinct system/Rose routes. They do not validate browser rendering, WebGL, gestures, animation, audio or physical vibration.

Nathan reviewed the prototype interactively. Independent browser verification by Prototyper remains unperformed: the recorded sandboxed Chromium helper-ownership failure and cloud-browser local-file navigation denial were not bypassed. Reduced-motion/static fallbacks are implemented but not browser-verified.

## Provenance

The HTML contains detailed reference/provenance notes. Its current icy glass/silver styling follows Nathan's supplied October 29 concept; the embedded wallpaper is an original generated matching interpretation, not an exact extraction. Glass-cube WebGL and SVG fallback are procedural approximations. Firefox, Thunderbird and VLC artwork came from official sources through the Illustrator archive; added generic system icons and app content are original fixtures. Historical Fold/Jade inlet notes in the HTML describe earlier iterations, not the current wallpaper.

Prior snapshots remain in the existing Library item's version history. This repository snapshot matches the delivered v15 HTML bytes.
