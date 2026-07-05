"""
Composite augmentation tool for AKSUMAEL YOLO dataset.

Pastes transparent-background PNG cutouts (e.g. a mob, item, or block sprite
with alpha channel) onto existing background frames at random positions,
scales, and optionally rotations -- then auto-generates the matching YOLO
label line for each composite.

This is meant to BOOTSTRAP a class that has few/no real in-game examples
(e.g. "creeper") by combining a clean cutout with realistic cave/scene
backgrounds from your existing dataset. It does NOT replace real captures --
real frames should always be the majority of training data for a class once
available. Composites are best used sparingly (a handful per class) to give
the model a starting signal.

Requirements:
    pip install pillow --break-system-packages   (pillow likely already installed)

Usage:
    python3 composite_augment.py \
        --cutout cutouts/creeper.png \
        --class-id 16 \
        --backgrounds data/yolo_dataset/images/train \
        --images-out data/yolo_dataset/images/train \
        --labels-out data/yolo_dataset/labels/train \
        --count 8 \
        --prefix synth_creeper \
        --hud-ref data/yolo_dataset/labels/train/frame000.txt \
        --hud-classes 0,1,2,3,4,5 \
        --scale-range 0.08,0.25 \
        --avoid-bottom 0.35

    --cutout         path to a PNG with alpha transparency (the object to paste)
    --class-id       YOLO class id to assign to the pasted object
    --backgrounds    directory of existing background images (.jpg/.png) to
                      paste onto. One is picked at random per composite.
    --images-out     output directory for composite images
    --labels-out     output directory for composite label .txt files
    --count          number of composites to generate (default 5)
    --prefix         filename prefix for outputs (default "synth")
    --hud-ref        optional reference label to copy HUD-class lines from,
                      so composites still have HUD boxes for those classes
    --hud-classes    comma-separated class IDs to copy from --hud-ref
    --scale-range    min,max scale of the cutout relative to background
                      width (default "0.05,0.20")
    --avoid-bottom   fraction of the background height (from the bottom) to
                      avoid placing the object in, so it doesn't overlap the
                      HUD (default 0.30)
    --rotate         max random rotation in degrees, +/- (default 0, no rotation)
    --seed           random seed for reproducibility (optional)
"""

import argparse
import os
import random
import glob

from PIL import Image


def load_hud_lines(ref_label_path, hud_class_ids):
    if not ref_label_path or not os.path.isfile(ref_label_path):
        return []
    if not hud_class_ids:
        return []
    keep = set(hud_class_ids)
    lines = []
    with open(ref_label_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            try:
                cid = int(parts[0])
            except (ValueError, IndexError):
                continue
            if cid in keep:
                lines.append(line)
    return lines


def parse_class_list(s):
    if not s:
        return set()
    return {int(tok.strip()) for tok in s.split(",") if tok.strip()}


def parse_float_pair(s, default):
    if not s:
        return default
    parts = s.split(",")
    return (float(parts[0]), float(parts[1]))


def next_index(out_dir, prefix, ext):
    import re
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)\.{ext}$")
    max_idx = 0
    if os.path.isdir(out_dir):
        for fname in os.listdir(out_dir):
            m = pattern.match(fname)
            if m:
                max_idx = max(max_idx, int(m.group(1)))
    return max_idx + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutout", required=True, help="PNG with alpha transparency")
    ap.add_argument("--class-id", type=int, required=True)
    ap.add_argument("--backgrounds", required=True, help="dir of background images")
    ap.add_argument("--images-out", required=True)
    ap.add_argument("--labels-out", required=True)
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--prefix", default="synth")
    ap.add_argument("--hud-ref", default=None)
    ap.add_argument("--hud-classes", default="")
    ap.add_argument("--scale-range", default="0.05,0.20",
                     help="min,max cutout width as fraction of background width")
    ap.add_argument("--avoid-bottom", type=float, default=0.30,
                     help="fraction of background height (from bottom) to avoid for placement")
    ap.add_argument("--rotate", type=float, default=0.0,
                     help="max random rotation in degrees, +/-")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    if not os.path.isfile(args.cutout):
        print(f"ERROR: cutout file not found: {args.cutout}")
        return

    cutout = Image.open(args.cutout).convert("RGBA")

    bg_files = sorted(
        glob.glob(os.path.join(args.backgrounds, "*.jpg")) +
        glob.glob(os.path.join(args.backgrounds, "*.png")) +
        glob.glob(os.path.join(args.backgrounds, "*.jpeg"))
    )
    if not bg_files:
        print(f"ERROR: no background images found in {args.backgrounds}")
        return

    os.makedirs(args.images_out, exist_ok=True)
    os.makedirs(args.labels_out, exist_ok=True)

    hud_class_ids = parse_class_list(args.hud_classes)
    hud_lines = load_hud_lines(args.hud_ref, hud_class_ids)
    scale_min, scale_max = parse_float_pair(args.scale_range, (0.05, 0.20))

    start_idx = next_index(args.images_out, args.prefix, "jpg")

    print(f"Generating {args.count} composite(s) using cutout: {args.cutout}")
    print(f"  class_id={args.class_id}  backgrounds_dir={args.backgrounds}")
    print(f"  scale_range=({scale_min},{scale_max})  avoid_bottom={args.avoid_bottom}")
    if hud_lines:
        print(f"  pre-seeding {len(hud_lines)} HUD label line(s) from {args.hud_ref}")
    print()

    for i in range(args.count):
        bg_path = random.choice(bg_files)
        bg = Image.open(bg_path).convert("RGBA")
        bw, bh = bg.size

        # scale cutout
        scale = random.uniform(scale_min, scale_max)
        target_w = int(bw * scale)
        aspect = cutout.height / cutout.width
        target_h = int(target_w * aspect)

        obj = cutout.resize((target_w, target_h), Image.LANCZOS)

        # optional rotation
        if args.rotate > 0:
            angle = random.uniform(-args.rotate, args.rotate)
            obj = obj.rotate(angle, expand=True, resample=Image.BICUBIC)
            target_w, target_h = obj.size

        # placement: avoid bottom strip (HUD area) and keep fully on-canvas
        max_y = int(bh * (1 - args.avoid_bottom)) - target_h
        max_y = max(max_y, 0)
        max_x = bw - target_w
        max_x = max(max_x, 0)

        x = random.randint(0, max_x)
        y = random.randint(0, max_y)

        composite = bg.copy()
        composite.alpha_composite(obj, (x, y))
        composite = composite.convert("RGB")

        idx = start_idx + i
        img_fname = f"{args.prefix}{idx:03d}.jpg"
        label_fname = f"{args.prefix}{idx:03d}.txt"

        composite.save(os.path.join(args.images_out, img_fname), quality=90)

        # YOLO normalized box for the pasted object
        cx = (x + target_w / 2) / bw
        cy = (y + target_h / 2) / bh
        w_norm = target_w / bw
        h_norm = target_h / bh

        with open(os.path.join(args.labels_out, label_fname), "w") as f:
            for line in hud_lines:
                f.write(line + "\n")
            f.write(f"{args.class_id} {cx:.4f} {cy:.4f} {w_norm:.4f} {h_norm:.4f}\n")

        print(f"  [{i+1}/{args.count}] {img_fname}  bg={os.path.basename(bg_path)}  "
              f"box=({cx:.3f},{cy:.3f},{w_norm:.3f},{h_norm:.3f})")

    print(f"\nDone. {args.count} composite(s) written to:")
    print(f"  images: {args.images_out}")
    print(f"  labels: {args.labels_out}")
    print("\nNOTE: composites supplement but should not replace real captures.")
    print("Keep composite count small relative to real examples for this class.")


if __name__ == "__main__":
    main()
