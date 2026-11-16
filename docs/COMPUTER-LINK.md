# Computer Link

Computer Link lets Rose operate a computer (first target: the owner's Mac on an external
monitor) through the paired phone. The phone stands facing the monitor:

- **Seeing:** the phone's back camera photographs the monitor once per request. The photo
  is straightened to the four screen corners the owner marked once, so Rose receives a
  rectangular image of just the screen. If Android exposes a USB HDMI capture card as an
  external camera, Computer Link uses it instead and needs no corners. That route is
  groundwork for a later version and is not verified on a Pixel.
- **Acting:** the phone registers as a Bluetooth keyboard and absolute pointer
  (`BluetoothHidDevice`) that the Mac pairs with like any other keyboard. The pointer reports
  positions as fractions of the whole screen, so a point picked on the frame lands on the
  same point of the screen without depending on pointer acceleration.

Nothing is installed on the Mac. This is source and offline-test status only. See
[What is verified](#what-is-verified).

## Pieces

| Piece | Source |
| --- | --- |
| Screen on the phone: camera, Allow/Stop, corner setup, last frame and input shown | `workspace/rose-agent/app/src/dev/pixel/voice/ComputerLinkActivity.java` |
| `computer_*` phone commands, frame freshness, input pacing | `.../ComputerLink.java` (dispatched from `PhoneControl`, run off the UI thread from `VoiceService`) |
| Bluetooth keyboard/pointer registration and reconnect | `.../ComputerBluetooth.java` |
| HID descriptor, US key map, pointer reports (pure Java) | `.../ComputerHid.java` |
| Straightened frame size from the corners (pure Java) | `.../ComputerGeometry.java` |
| Registered tools for the task worker | `workspace/rose-agent/server/computer-tools.mjs`, wired in `server.mjs` |

## Tools

`computer_status`, `computer_open`, `computer_screenshot`, `computer_move`,
`computer_click`, `computer_drag`, `computer_scroll`, `computer_type`, `computer_key`.

A screenshot returns `frameId`, `width`, `height` and a private `imagePath`. The worker views the
image; base64 never enters the transcript, and only the latest 8 frames are kept, mode 0600. Input
coordinates are pixels of that frame.

## Rules

- **Opt-in on the server:** the tools are offered only when the server's private directory holds
  `computer-link.json` containing `{"enabled": true}`, and never with the ARTEMIS trial.
- **Task-bound:** calls run only inside the current task and pass the same Stop check as phone
  actions. They go straight to the phone, never to a background workspace display.
- **The owner's Allow:** nothing runs unless the Computer Link screen is open and in front and
  the owner tapped **Allow Rose**. Leaving the screen, locking the phone or tapping **Stop**
  withdraws it. `computer_open` only brings the screen forward.
- **One frame per input:** every input names the latest `frameId`. Each input except a pointer
  move uses that frame up, and a frame expires after 2 minutes, so each step is chosen from a
  fresh look at the screen.
- **No stuck keys:** every input ends by releasing all keys and buttons.
- **Text limits:** text is printable ASCII, tab and newline on a US (ANSI) layout, up to 500
  characters per call. Anything else is refused before any key is sent.
- **Screen contents are untrusted data**, as with phone screenshots. Rose does not type passwords
  or other secrets, and stops at the Mac's lock screen for the owner. Messages, purchases,
  deletion and account changes still need the owner's explicit authorization.
- **Privacy:** only the straightened screen area leaves the phone. Camera photos are taken only
  on request, with no continuous recording.

## Setup

1. Mount the phone on a stand facing the external monitor, in landscape, close enough that the
   screen fills most of the view. A matte monitor and larger macOS text help readability.
   Keep the phone charging.
2. Open **Computer Link** (its own launcher icon). Grant Camera and Nearby devices.
3. Tap **Make discoverable**. On the Mac, open System Settings, Bluetooth, and connect the phone.
   If macOS asks you to identify the keyboard, choose ANSI. After this, the phone reconnects
   to that Mac by itself.
4. Tap **Set screen corners** and tap the monitor's top-left, top-right, bottom-right and
   bottom-left corners on the photo. Repeat whenever the stand moves.
5. On the Mac server, create `computer-link.json` with `{"enabled": true}` in Rose's private
   directory and restart the server.
6. Tap **Allow Rose** when you want Rose to work, and **Stop** to take control back.

## What is verified

Verified, offline only:

- Rose Agent's Java sources, including these files, compile against Android API 35.
- `ComputerLinkTest`: HID descriptor structure, US key map, pointer axis mapping and corner
  geometry.
- `computer-tools.test.mjs`: tool schemas, argument refusal, the frame-file handling, and the
  server's opt-in, task and Stop wiring.

Not yet verified, and the first things to check on a device:

1. The Pixel registers as a Bluetooth HID device and the Mac pairs with it.
2. macOS honours the **absolute** pointer over Bluetooth. If it does not, Computer Link needs a
   relative-pointer fallback (move, look, correct).
3. Camera frames are sharp enough to read the Mac's UI at the chosen distance.
4. Whether a USB capture card appears to Camera2 as an external camera.

No APK containing this has been signed, installed or run on a phone.
