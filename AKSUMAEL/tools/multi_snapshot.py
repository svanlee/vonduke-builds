"""
Multi-snapshot capture tool for AKSUMAEL YOLO dataset building.

Captures frames from the Rybozen capture card at a fixed interval,
saves them as images, and auto-generates a starting label file for
each new frame by copying the HUD-class boxes from a reference label
(since HUD elements like health/hunger/armor/xp/hotbar/crosshair sit
in roughly the same screen position across frames).

World-object classes (chest, bed, torch, fire, etc.) are NOT copied
since their position/presence varies per frame -- those lines need to
be added/edited by hand for each new frame.

Usage:
    python3 multi_snapshot.py --count 20 --interval 3 \
        --images-out data/yolo_dataset/images/train \
        --labels-out data/yolo_dataset/labels/train \
        --ref-label data/yolo_dataset/labels/train/frame001.txt \
        --hud-classes 0,1,2,3,4,5

    --count        number of frames to capture (default 10)
    --interval     seconds between captures (default 2)
    --images-out   output directory for images (default ".")
    --labels-out   output directory for label .txt files (default same as images-out)
    --ref-label    reference label file to copy HUD boxes from (optional)
    --hud-classes  comma-separated class IDs to auto-copy from ref-label
                   (default: none -- if omitted, label files are not pre-seeded)
    --prefix       filename prefix (default "frame")
    --start        starting index for filenames (default: auto-detect next free index)
    --device       /dev/videoN (default /dev/video0)
"""

import argparse
import os
import time
import re
import cv2


def next_start_index(out_dir, prefix, ext):
    """Find the next free frameNNN index so repeated runs don't overwrite files."""
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)\.{ext}$")
    max_idx = 0
    if os.path.isdir(out_dir):
        for fname in os.listdir(out_dir):
            m = pattern.match(fname)
            if m:
                max_idx = max(max_idx, int(m.group(1)))
    return max_idx + 1


def load_hud_lines(ref_label_path, hud_class_ids):
    """Read the reference label file and return only lines whose class id is in hud_class_ids."""
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
    result = set()
    for tok in s.split(","):
        tok = tok.strip()
        if tok:
            result.add(int(tok))
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=10, help="number of frames to capture")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between captures")
    ap.add_argument("--images-out", type=str, default=".", help="output directory for images")
    ap.add_argument("--labels-out", type=str, default=None,
                     help="output directory for label .txt files (default: same as --images-out)")
    ap.add_argument("--ref-label", type=str, default=None,
                     help="reference label file to copy HUD-class boxes from")
    ap.add_argument("--hud-classes", type=str, default="",
                     help="comma-separated class IDs to auto-copy from --ref-label")
    ap.add_argument("--prefix", type=str, default="frame", help="filename prefix")
    ap.add_argument("--start", type=int, default=None, help="starting index (default: auto)")
    ap.add_argument("--device", type=str, default="/dev/video0", help="capture device")
    args = ap.parse_args()

    images_out = args.images_out
    labels_out = args.labels_out or images_out
    os.makedirs(images_out, exist_ok=True)
    os.makedirs(labels_out, exist_ok=True)

    hud_class_ids = parse_class_list(args.hud_classes)
    hud_lines = load_hud_lines(args.ref_label, hud_class_ids)

    if hud_class_ids and not hud_lines:
        print(f"WARNING: --hud-classes {sorted(hud_class_ids)} given but no matching "
              f"lines found in {args.ref_label!r}. Label files will be created empty.")
    elif hud_lines:
        print(f"Auto-copying {len(hud_lines)} HUD label line(s) "
              f"(classes {sorted(hud_class_ids)}) from {args.ref_label} into each new frame.")
    else:
        print("No --ref-label/--hud-classes given -- label files will not be pre-seeded.")

    # index images and labels independently so this is safe to re-run
    img_start_idx = args.start if args.start is not None else next_start_index(images_out, args.prefix, "jpg")

    cap = cv2.VideoCapture(args.device)
    if not cap.isOpened():
        print(f"Failed to open {args.device} -- try /dev/video1")
        return

    print(f"Capturing {args.count} frames every {args.interval}s")
    print(f"  images -> {images_out}")
    print(f"  labels -> {labels_out}")
    print(f"Starting at index {img_start_idx:03d}")
    print("Switch to your game window now. Capturing begins immediately.")

    saved = []
    for i in range(args.count):
        # flush a couple of stale buffered frames for a fresher capture
        for _ in range(2):
            cap.read()
        ret, frame = cap.read()
        if not ret:
            print(f"  [{i+1}/{args.count}] capture failed, skipping")
            time.sleep(args.interval)
            continue

        idx = img_start_idx + i
        img_fname = f"{args.prefix}{idx:03d}.jpg"
        label_fname = f"{args.prefix}{idx:03d}.txt"
        img_fpath = os.path.join(images_out, img_fname)
        label_fpath = os.path.join(labels_out, label_fname)

        cv2.imwrite(img_fpath, frame)

        # Pre-seed label file with HUD-class lines (or create empty file as a placeholder)
        with open(label_fpath, "w") as f:
            for line in hud_lines:
                f.write(line + "\n")

        saved.append((img_fname, label_fname))
        note = f"({len(hud_lines)} HUD lines pre-filled)" if hud_lines else "(empty label - add boxes manually)"
        print(f"  [{i+1}/{args.count}] saved {img_fname}  shape={frame.shape}  {note}")

        if i < args.count - 1:
            time.sleep(args.interval)

    cap.release()

    print(f"\nDone. Saved {len(saved)} frame(s):")
    for img_fname, label_fname in saved:
        print(f"  {img_fname}  ->  {label_fname}")

    if hud_lines:
        print("\nNext: open each new label .txt and ADD lines for any world objects "
              "visible in that frame (chest, bed, torch, fire, etc). "
              "HUD-class boxes are already filled in.")
    else:
        print("\nNext: label each new frame from scratch (no HUD reference was used).")


if __name__ == "__main__":
    main()
