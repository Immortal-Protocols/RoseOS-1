# Phonon voice catalog expansion

This source change extends the existing Phonon 31M catalog from nine to 25
ordinary English voices. It preserves the nine stable names and asset IDs,
Zoey as the default, identity storage, and the existing playback/cancellation
path. It does not deploy a service, change a phone build, subscribe to a hosted
provider, or admit character voices.

## Source and measured data

`prototypes/voice-phone-demo/rose/voice_catalog.json` is the catalog authority.
It records stable names, explicit language/locale/gender/category, sample
folders, asset SHA-256 values and the pinned upstream revision
`0ee17205f254b5abc43b266c8a0f215565c352f7` of
[gradium/phonon-voices](https://huggingface.co/gradium/phonon-voices/tree/0ee17205f254b5abc43b266c8a0f215565c352f7).
The model stays [Phonon 31M English](https://huggingface.co/gradium/phonon-31m-en),
with Apache-2.0 model/catalog licensing. The differently licensed 40M models are
not a substitute in this change.

The sixteen added candidates are Trevor, Russell, Preston, Maeve, Freya,
Pippa, Elodie-Rose, Imogen, Lottie, Bryony, Alfie, Hugh, Edwin, Spencer, Toby and
Teddy. Three are cataloged as American English and thirteen as British English.
These broad provider labels do not establish a regional accent, accent strength,
or perceptual quality. Each new record remains `pending_listening` metadata.
This is an acceptance status, not a feature flag: source testing and listening
must finish before production admission.

`voice_traits.json` contains acoustic proxies from the same five upstream
reference prompts for all 25 voices: `exclamation_02`, `happy_01`, `question_01`,
`support_01`, and `support_04`. The original nine records are unchanged. The
sixteen new records were imported from already-completed research, not measured
again during this implementation. The imported source file SHA-256 is
`6e92712f8272dcca75d86278fe54e66470e9f45bf7008c31b91a422d6e8d7982`.
The original nine-record file SHA-256 is
`4d0f498deb288cce531ef2b31844b8db9c6af2ac81cf3c8624a991c847165b8b`.
Each record retains its reference sample hashes. The separate research experiment
using two generated prompts, and its character voices, are not mixed into this
dataset.

The measurement script reads explicit metadata rather than parsing gender from
a folder name. A missing common prompt or an unmeasurable spectrum is an error;
a shorter corpus must never be reported as a faster voice. This implementation
updated the script without rerunning measurements or synthesis.

## Loading and selection

The Mac loader requests the exact pinned revision and checks the asset hash
before model loading. A corrupt or missing asset fails without choosing another
engine. The cloud adapter has a generated copy of the IDs and hashes. Its
existing configured-local-file allowlist is unchanged; adding catalog data does
not download cloud assets or activate the paused cloud serving route.

The conversation tool supports `same`, `american`, `british`, `either`, and
`other` plus the requested wording. An omitted accent preserves the current one.
Exclusions are constraints, not preferences. Unsupported accents or absent
configured voices keep the current voice and explain the limit; an American
voice is never a fallback for a British-only request. Acoustic directions are
ranked only among allowed, measured, ordinary voices. A gender-qualified request
is compared against that gender's available voices in the allowed locale.

Successful changes still request one audible confirmation sentence, refinement,
and previous/default revert. A load failure is not success. A save failure tries
to restore the old live voice; if restoration also fails, the response explicitly
reports that the live and saved choices may differ.

## Offline checks

From the repository root, with the existing non-MLX voice test dependencies:

```sh
PYTHONPATH=prototypes/voice-phone-demo python -m unittest \
  rose.tests.test_voice_identity rose.tests.test_voice_catalog \
  rose.tests.test_codex rose.tests.test_tailnet rose.tests.test_vision_phone
python prototypes/voice-phone-demo/scripts/sync_voice_catalog.py --check
bash scripts/test-local.sh
```

The catalog tests use synthetic fake speakers for control-flow checks and never
run synthesis or sample measurement. They cover all 25 measured inventory entries,
constraints, persistence/revert, pinned loading, hash rejection, failure reporting,
and the generated cloud-data mirror. CI runs the new catalog module alongside the
existing voice regressions.

### Development verification, November 10, 2026

On an isolated Linux source checkout based on `2532ddc5`, Python 3.12.14,
Node 24.22.1 and Java 21.0.13:

- The final combined voice/catalog/identity/Codex/tailnet/vision-phone run passes
  224 tests, including 92 catalog and identity tests. These use fake engines and
  synthetic control-flow fixtures, not live model or phone acceptance.
- The current-source speech-wire suite passes all nine tests with a synthetic
  TTS engine, and cloud preflight passes ten tests.
- Workflow path coverage passes six tests; the generated catalog mirror check
  and whitespace checks pass.
- The aggregate offline suite is **not a pass**. Its first attempt stopped at a
  missing `javac` launcher. A scoped launcher using the already-installed Java
  compiler module allowed the rerun to proceed, but unchanged release-pipeline
  fixtures then stopped because `/usr/sbin/mke2fs` is absent. Two earlier
  SDK-dependent switcher cases and two signing cross-checks requiring the local
  prototype virtualenv were skipped. No system tools were installed and
  no release, contract regeneration, signing or deployment was attempted.

Independent review verified the original and imported data hashes and found
accent-exclusion edge cases. Regressions now cover prefixed negated lists,
exclusions after alternatives, and the difference between “not American English”
and “neither American nor English.” Contradictory keep/exclusion requests retain
the current voice, and curly-apostrophe negations behave like plain apostrophes.

## Remaining acceptance

Source tests do not establish perceptual distinction or usable audio quality.
Comparable listening of the sixteen ordinary candidates, name/contraction and
long-reply quality, actual Mac cold/warm timing under representative load, and
Stop/late-buffered-audio acceptance remain outstanding. Existing research host
timings are not Mac or end-to-end playback evidence. Producer deployment belongs
to the existing Mac owner; phone publication remains on the normal Frankel path.
