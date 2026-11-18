#!/usr/bin/env python3
"""Build the shipped outfit gallery from outfit specs and their rendered images.

    python scripts/build_outfit_gallery.py \\
        --specs scripts/outfits/outfit_specs.json \\
        --renders .cache/outfit-renders --dest assets/Outfits

Each spec with a render becomes img/<id>.webp, thumbs/<id>.webp and a catalog row
(id, label, prompt, category, tags, tag_groups). Specs without a render are listed
and skipped, so a partial batch still builds. Use --exclude for renders that failed review.
"""
import argparse
import json
import shutil
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
THUMB_EDGE = 512
FIELDS = ("id", "label", "prompt", "category", "tags", "tag_groups")


def build(specs, renders: Path, dest: Path, exclude=(), quality=90):
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "img").mkdir(parents=True)
    (dest / "thumbs").mkdir()
    rows, missing = [], []
    for spec in specs:
        if spec["id"] in exclude:
            continue
        source = renders / f"{spec['id']}.png"
        if not source.is_file():
            missing.append(spec["id"])
            continue
        with Image.open(source) as image:
            image = image.convert("RGB")
            image.save(dest / "img" / f"{spec['id']}.webp", "WEBP", quality=quality, method=6)
            thumb = image.copy()
            thumb.thumbnail((THUMB_EDGE, THUMB_EDGE))
            thumb.save(dest / "thumbs" / f"{spec['id']}.webp", "WEBP", quality=80, method=6)
        row = {key: spec[key] for key in FIELDS if key in spec}
        row["image"] = f"img/{spec['id']}.webp"
        row["thumbnail"] = f"thumbs/{spec['id']}.webp"
        rows.append(row)
    catalog = {"schema_version": 1, "pose": [], "outfit": rows}
    (dest / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return rows, missing


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--specs", type=Path, default=ROOT / "scripts/outfits/outfit_specs.json")
    parser.add_argument("--renders", type=Path, default=ROOT / ".cache/outfit-renders")
    parser.add_argument("--dest", type=Path, default=ROOT / "assets/Outfits")
    parser.add_argument("--exclude", nargs="*", default=[], help="Outfit ids to leave out")
    args = parser.parse_args()
    specs = json.loads(args.specs.read_text(encoding="utf-8"))
    rows, missing = build(specs, args.renders, args.dest, set(args.exclude))
    print(json.dumps({"outfits": len(rows), "missing_renders": missing, "excluded": args.exclude}))


if __name__ == "__main__":
    main()
