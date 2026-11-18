#!/usr/bin/env python3
"""Turn a finished Pose Studio run into a small, shippable folder gallery.

Only the mannequin drawings, their saved depth maps and the reviewed catalog
rows are written. Source photos, comparisons, history, trash and batch receipts
(which hold local paths) stay behind. Usage:

    python scripts/bundle_gallery.py SOURCE_RUN_DIR

writes assets/shipped/<run name>/, and everything in assets/shipped/ ships with the pack.
Pass a second path to write somewhere else, for example "assets/My Poses" to keep it local.
"""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

from PIL import Image

THUMB_EDGE = 512


def bundle(source: Path, dest: Path, quality: int = 90) -> dict:
    catalog = json.loads((source / "catalog.json").read_text(encoding="utf-8"))
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "img" / "depth").mkdir(parents=True)
    (dest / "thumbs").mkdir()
    kept, skipped, depths = [], [], 0
    for entry in catalog.get("pose", []):
        if str(entry.get("label", "")).startswith("Unreviewed") or entry.get("category") == "Unreviewed":
            skipped.append(entry["id"])  # never captioned, or flagged for pose review
            continue
        drawing = source / entry["image"]
        stem = Path(entry["image"]).stem
        with Image.open(drawing) as image:
            image.load()
            image.save(dest / "img" / f"{stem}.webp", "WEBP", quality=quality, method=6)
            thumb = image.copy()
            thumb.thumbnail((THUMB_EDGE, THUMB_EDGE))
            thumb.save(dest / "thumbs" / f"{stem}.webp", "WEBP", quality=80, method=6)
        # Pose Studio names depth maps by the hash of the drawing PNG; ship them
        # beside the converted drawing instead, under the same file name.
        digest = hashlib.sha256(drawing.read_bytes()).hexdigest()
        depth = source / "depth" / f"{digest}.png"
        if depth.is_file():
            with Image.open(depth) as image:
                image.save(dest / "img" / "depth" / f"{stem}.webp", "WEBP", lossless=True, method=6)
            depths += 1
        row = {k: v for k, v in entry.items() if k not in ("image", "thumbnail")}
        row["image"] = f"img/{stem}.webp"
        row["thumbnail"] = f"thumbs/{stem}.webp"
        kept.append(row)
    if not depths:
        (dest / "img" / "depth").rmdir()
    out = {"schema_version": catalog.get("schema_version", 1), "pose": kept, "outfit": []}
    (dest / "catalog.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"kept": len(kept), "skipped": skipped, "depth_maps": depths}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="Pose Studio run folder (has catalog.json and img/)")
    parser.add_argument("dest", type=Path, nargs="?", help="Output gallery folder (default: assets/shipped/<run name>)")
    parser.add_argument("--quality", type=int, default=90)
    args = parser.parse_args()
    dest = args.dest
    if dest is None:
        manifest = args.source / "studio.json"
        label = json.loads(manifest.read_text(encoding="utf-8")).get("label") if manifest.is_file() else None
        name = " ".join(str(label or args.source.resolve().name).split())
        if not name or name.startswith(".") or "/" in name or "\\" in name:
            parser.error("Name the gallery: pass an output folder such as assets/shipped/My Poses.")
        dest = Path(__file__).resolve().parents[1] / "assets/shipped" / name
    print(json.dumps({"dest": str(dest), **bundle(args.source, dest, args.quality)}))


if __name__ == "__main__":
    main()
