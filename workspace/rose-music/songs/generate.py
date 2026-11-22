#!/usr/bin/env python3
"""Generate the launch catalog with ACE-Step 1.5 XL Turbo.

Run from an ACE-Step-1.5 checkout (it needs that project's environment):

    cd ~/ACE-Step-1.5
    uv run python /path/to/workspace/rose-music/songs/generate.py --out /path/to/out

Writes one MP3 per track plus `generation.json`, a record of the prompts, seeds,
model and timings used. Audio is written outside Git; only this script and the
prompt list are tracked.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time

STYLE = (
    "Instrumental progressive math rock with virtuosic clean electric guitar: "
    "intricate two-handed tapping, hybrid-picked arpeggios, sparkling chorus-drenched "
    "clean tones, sudden time feels, nylon-string flourishes, slap bass, tight "
    "trap-influenced drums with crisp hi-hat rolls and deep 808 hits. Polished modern "
    "production, wide stereo, playful and technical, no vocals."
)

# title, seconds, bpm, key, mood/arrangement detail, seed
TRACKS = [
    ("Glass Orchard", 52, 140, "E major",
     "a short bright opener; a single tapped guitar motif blooms into a full band hit", 1101),
    ("Neon Koi", 196, 150, "F# minor",
     "bouncy syncopated riff, call-and-response between two guitars, half-time trap breakdown", 1102),
    ("Velvet Static", 128, 92, "D major",
     "laid-back neo-soul groove, warm chords, a lyrical melodic solo over lo-fi drums", 1103),
    ("Paper Lanterns", 74, 120, "A major",
     "delicate nylon-string intro, fingerpicked harmonics, gentle build to a shimmering finish", 1104),
    ("Hyperbloom", 238, 160, "C# minor",
     "relentless high-energy tapping, odd-meter sections, a heavy djent-style chug drop and soaring finale", 1105),
    ("Sugar Circuit", 98, 128, "G major",
     "funky staccato chords, slap bass hooks, glitchy stutter edits, very danceable", 1106),
    ("Moth Logic", 162, 110, "B minor",
     "moody and cinematic, ambient swells, polyrhythmic arpeggios that lock into a tight groove", 1107),
    ("Kintsugi", 210, 135, "E minor",
     "emotional and dynamic, clean verses, explosive distorted climax, sweep-picked lead line", 1108),
    ("Afterglow Arcade", 64, 170, "D# minor",
     "fast chiptune-flavoured runs on guitar, playful, ends on a sudden stop", 1109),
    ("Halcyon Drift", 276, 100, "F major",
     "long-form journey, starts dreamy and sparse, evolves through jazzy chords to a triumphant tapped outro", 1110),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", type=int, nargs="*", help="1-based track numbers to (re)generate")
    ap.add_argument("--lm", default="acestep-5Hz-lm-1.7B")
    args = ap.parse_args()

    root = os.getcwd()
    sys.path.insert(0, root)
    from acestep.handler import AceStepHandler
    from acestep.llm_inference import LLMHandler
    from acestep.inference import GenerationParams, GenerationConfig, generate_music

    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    work = os.path.join(out, "_work")
    os.makedirs(work, exist_ok=True)

    dit = AceStepHandler()
    msg, ok = dit.initialize_service(project_root=root, config_path="acestep-v15-xl-turbo",
                                     device="auto", offload_to_cpu=False)
    if not ok:
        print("DiT init failed:", msg, file=sys.stderr)
        return 1
    lm = LLMHandler()
    msg, ok = lm.initialize(checkpoint_dir=os.path.join(root, "checkpoints"), lm_model_path=args.lm,
                            backend="mlx" if platform.machine() == "arm64" else "pt",
                            device="auto", offload_to_cpu=False, dtype=None)
    if not ok:
        print("LM init failed:", msg, file=sys.stderr)
        return 1

    record_path = os.path.join(out, "generation.json")
    record = json.load(open(record_path)) if os.path.exists(record_path) else {"tracks": {}}
    record.update({
        "model": "ACE-Step/acestep-v15-xl-turbo",
        "lm": args.lm,
        "acestep_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                         text=True).stdout.strip(),
        "style": STYLE,
    })

    for n, (title, secs, bpm, key, detail, seed) in enumerate(TRACKS, 1):
        if args.only and n not in args.only:
            continue
        caption = f"{STYLE} {detail[0].upper()}{detail[1:]}."
        params = GenerationParams(
            task_type="text2music", thinking=True, caption=caption, lyrics="[Instrumental]",
            instrumental=True, bpm=bpm, keyscale=key, timesignature="4", vocal_language="unknown",
            duration=secs, inference_steps=8, guidance_scale=1.0, seed=seed,
        )
        config = GenerationConfig(batch_size=1, use_random_seed=False, seeds=[seed], audio_format="mp3")
        t0 = time.time()
        result = generate_music(dit, lm, params=params, config=config, save_dir=work)
        took = time.time() - t0
        if not result.success or not result.audios:
            print(f"[{n}] {title} FAILED: {result.status_message} {result.error}", file=sys.stderr)
            return 2
        src = result.audios[0]["path"]
        dst = os.path.join(out, f"{n:02d}.mp3")
        shutil.move(src, dst)
        record["tracks"][str(n)] = {"title": title, "duration_s": secs, "bpm": bpm, "key": key,
                                    "seed": seed, "caption": caption, "seconds_to_generate": round(took, 1),
                                    "file": os.path.basename(dst)}
        json.dump(record, open(record_path, "w"), indent=2)
        print(f"[{n}] {title}: {dst} in {took:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
