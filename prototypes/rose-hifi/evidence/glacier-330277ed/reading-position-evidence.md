# Reading-position and return evidence — Glacier 1E @ 330277ed

Bound run: glacier-check.json in this folder (clean tree; tested Rose-Glacier.html git blob d535073d0210fedf3165fc9c2edf54cafdca9ec7 == committed; SHA-256 53b7af930a81144be370c96d11839796757897e5586f3a14899f61a9683bf6ae; 234/234 checks; captured 2026-11-10T02:41:33.234Z).
Headless Chromium + SwiftShader; phones are Chromium emulation with CDP touch. Browser-emulated proof only — not physical-phone, Safari, native IME or native acceptance.

## Rules

- Messages follows the newest message only while the person is at the bottom, or after a send.
- The person's own scroll input (wheel, touch, pointer drag, scroll keys) always decides — even while the keyboard/chin animate — and pauses bottom-pinning. Scrolls caused only by resizing are ignored.
- Departure to Rose captures scroll offset, follow state, draft, selection and keyboard; only an acknowledged V3 return to the exact live task restores them, re-validated at the moment of departure.

## Checks

- PASS design: tap requests return directly → Returning… with a separate Cancel
- PASS design: repeat taps while pending are ignored
- PASS design: no departure without acknowledgement (Rose stays usable)
- PASS design: Cancel invalidates the request and stays in Rose
- PASS design: acknowledged failure → “Could not return.” sheet; stays in Rose; keyboard hidden
- PASS design: closed origin → “Your source app is unavailable.” with Stay / Open switcher / Go Home; no relaunch, no keyboard
- PASS design: replaced task (new generation) is not the exact live task → unavailable
- PASS design: acknowledged success departs to the exact app with draft, selection and keyboard
- PASS design: reading position restored
- PASS design: Rose work keeps running through the return
- PASS design: hiding the keyboard never sends or dismisses; draft kept
- PASS design: failure: “Couldn’t send · Retry”, draft kept
- PASS iphone13-390: tap requests return directly → Returning… with a separate Cancel
- PASS iphone13-390: repeat taps while pending are ignored
- PASS iphone13-390: no departure without acknowledgement (Rose stays usable)
- PASS iphone13-390: Cancel invalidates the request and stays in Rose
- PASS iphone13-390: acknowledged failure → “Could not return.” sheet; stays in Rose; keyboard hidden
- PASS iphone13-390: closed origin → “Your source app is unavailable.” with Stay / Open switcher / Go Home; no relaunch, no keyboard
- PASS iphone13-390: replaced task (new generation) is not the exact live task → unavailable
- PASS iphone13-390: acknowledged success departs to the exact app with draft, selection and keyboard
- PASS iphone13-390: reading position restored
- PASS iphone13-390: Rose work keeps running through the return
- PASS iphone13-390: hiding the keyboard never sends or dismisses; draft kept
- PASS iphone13-390: failure: “Couldn’t send · Retry”, draft kept
- PASS iphoneSE-320: tap requests return directly → Returning… with a separate Cancel
- PASS iphoneSE-320: repeat taps while pending are ignored
- PASS iphoneSE-320: no departure without acknowledgement (Rose stays usable)
- PASS iphoneSE-320: Cancel invalidates the request and stays in Rose
- PASS iphoneSE-320: acknowledged failure → “Could not return.” sheet; stays in Rose; keyboard hidden
- PASS iphoneSE-320: closed origin → “Your source app is unavailable.” with Stay / Open switcher / Go Home; no relaunch, no keyboard
- PASS iphoneSE-320: replaced task (new generation) is not the exact live task → unavailable
- PASS iphoneSE-320: acknowledged success departs to the exact app with draft, selection and keyboard
- PASS iphoneSE-320: reading position restored
- PASS iphoneSE-320: Rose work keeps running through the return
- PASS iphoneSE-320: hiding the keyboard never sends or dismisses; draft kept
- PASS iphoneSE-320: failure: “Couldn’t send · Retry”, draft kept
- PASS short-390x600: tap requests return directly → Returning… with a separate Cancel
- PASS short-390x600: repeat taps while pending are ignored
- PASS short-390x600: no departure without acknowledgement (Rose stays usable)
- PASS short-390x600: Cancel invalidates the request and stays in Rose
- PASS short-390x600: acknowledged failure → “Could not return.” sheet; stays in Rose; keyboard hidden
- PASS short-390x600: closed origin → “Your source app is unavailable.” with Stay / Open switcher / Go Home; no relaunch, no keyboard
- PASS short-390x600: replaced task (new generation) is not the exact live task → unavailable
- PASS short-390x600: acknowledged success departs to the exact app with draft, selection and keyboard
- PASS short-390x600: reading position restored
- PASS short-390x600: Rose work keeps running through the return
- PASS short-390x600: hiding the keyboard never sends or dismisses; draft kept
- PASS short-390x600: failure: “Couldn’t send · Retry”, draft kept
- PASS departure race (closed during the 200 ms departure): stays in Rose with the unavailable sheet; no keyboard
- PASS departure race (replaced during the 200 ms departure): stays in Rose with the unavailable sheet; no keyboard
- PASS departure race (home during the 200 ms departure): Home wins; no stale return
- PASS departure race (control during the 200 ms departure): acknowledged return departs
- PASS a scroll up during the keyboard animation is kept (not re-pinned to the bottom)

Composite contrast in this run: text >= 5.90:1, glyphs >= 10.09:1 (5th percentile of rendered background pixels; transitions frozen).
