#!/usr/bin/env python3
"""Cover art for the launch catalog with Z-Image Turbo (mflux, Apple Silicon).

    PYTHONPATH= ~/prometheus-worlds/spike/.venv/bin/python covers.py --out DIR

Any Python with `mflux` (0.24+) works. Writes DIR/NN.jpg (1024x1024).
"""

import argparse
import os
import sys

SERIES = ("Album cover art, square, no text, no letters, no typography. A cohesive series: "
          "dreamlike iridescent glass and liquid chrome forms, soft pastel neon light, "
          "fine grain, shallow depth of field, elegant minimal composition, high-end editorial. ")

COVERS = [
    "A small orchard of translucent glass trees with glowing fruit at dawn, mint and peach light.",
    "Two luminous koi made of neon light circling in dark water, magenta and cyan reflections.",
    "Crushed velvet folds in deep plum with tiny sparks of static electricity, warm gold rim light.",
    "Floating paper lanterns over a calm lake at dusk, soft amber glow, mist.",
    "An enormous chrome flower exploding into bloom, petals shattering into prisms, electric violet.",
    "Glossy candy-coloured circuit board traces forming a maze, bubblegum pink and lime.",
    "A pale moth with geometric wing patterns resting on a glowing lattice, moonlight blue.",
    "A cracked porcelain bowl repaired with molten gold seams, dramatic dark background.",
    "A retro arcade cabinet glowing in an empty pastel room at sunset, pixel light trails.",
    "A lone sailboat drifting on a mirror-calm sea under a giant soft sun, lavender haze.",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", type=int, default=1024)
    args = ap.parse_args()
    from mflux.models.z_image import ZImageTurbo

    os.makedirs(args.out, exist_ok=True)
    model = ZImageTurbo(quantize=8)
    for n, prompt in enumerate(COVERS, 1):
        dst = os.path.join(args.out, f"{n:02d}.jpg")
        if os.path.exists(dst):
            continue
        img = model.generate_image(seed=2200 + n, prompt=SERIES + prompt, num_inference_steps=8,
                                   width=args.size, height=args.size)
        img.image.convert("RGB").save(dst, quality=90)
        print(f"[{n}] {dst}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
