# Real d8 output for the raised-edge dex reader

`scripts/edge_artifact.py` reads the dex files of the target-files APKs itself, with no dexdump. These two files are real
d8 output, so its tests also run against dex as the toolchain writes it, not only `dex_fixture.py`'s synthetic files.

`src/` holds four stand-ins: small classes with the names, methods and literals of the raised edge's real code. They are
not the RoseOS sources, which need the platform classpath. Both files come from the same class files:

    javac -source 11 -target 11 -d classes $(find src -name '*.java')       # javac 21.0.12.1
    d8 --min-api 28 --output out39 classes/**/*.class                       # d8-039.dex
    java -Dcom.android.tools.r8.dexContainerExperiment=true -cp d8.jar \
      com.android.tools.r8.D8 --min-api 34 --output out41 classes/**/*.class  # d8-041.dex

d8 is `D8 8.6.2-dev`, from SDK build-tools 35.0.0 (`android-15`).

The repository ignores `*.dex`, so each file is committed as base64 text: `d8-039.dex.b64` and `d8-041.dex.b64`.
`base64 -d d8-039.dex.b64 > d8-039.dex` restores the bytes. The tests decode each file and check it against its sha256:

| File | Format | sha256 of the decoded bytes |
| --- | --- | --- |
| `d8-039.dex` | dex 039 | `90eab09dea639d7846bcbc9d73c666e66a4c101b724a371c8378d90187101366` |
| `d8-041.dex` | dex 041 container, one section | `11de081491f2f85ee3a4ffbc70b3ee20488017098d4b1c49670a109aea258deb` |

`dexdump -l plain` from the same build-tools lists the same four classes and the same methods as the reader:

| Class | Methods |
| --- | --- |
| `RoseEdgeGesture` | `<init>`, `stage` |
| `RoseEdgeSwipe` | `<init>`, `diag`, `onMove` |
| `RoseEdgeMicSlot` | `<init>`, `tag` |
| `RoseEdgeController` | `<init>`, `descriptor`, `raise` |

Its fields (`DIAG_PROPERTY`, `mStage`, `TAG`, `DESCRIPTOR`) are not methods, and the reader does not count them.

Two larger comparisons were run once, offline, and are not committed:
- **Several sections.** A d8 041 container with 70,000 methods split into three sections. Each section's offsets are from
  the start of the file, and all three share one string table at the end.
- **A real-world dex.** The R8 jar dexed both ways: 12,262 classes as 039 and 12,253 as a 041 container.

In both, the reader and dexdump list the same classes and methods, with no mismatches.
