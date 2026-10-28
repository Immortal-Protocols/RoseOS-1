AgentTrayPrototype.java is the tray overlay file of the 2026102805 candidate, byte-identical at its source
7fb94ae6ed535066d50879a99b7c8e427da8e853 and at eca1fef9 (the parent of ceb6316, which added AgentTrayLink.java):
`git show 7fb94ae6:os/overlays/frameworks/base/libs/WindowManager/Shell/src/com/android/wm/shell/AgentTrayPrototype.java`,
sha256 2e3e86cb93f17aa2e3c56a4f0a004d19a8e37cf5fec24efb777f0b7df5e38c21. StatusVoiceGlow.java and
AgentStatusControls.java are unchanged since then and AgentTrayLink.java did not exist, so the reconcile test builds the
previous candidate's overlay from this file plus the current copies of those two.
