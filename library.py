"""Read a local reference catalog. No downloads or code evaluation."""

import json
import os
from functools import lru_cache
from pathlib import Path

NONE = "None"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
DEFAULT_ROOT = Path(__file__).resolve().parent / "assets" / "gallery"


def library_root():
    configured = os.environ.get("POSE_GENERATOR_ASSETS")
    if configured:
        return Path(configured).expanduser().resolve()
    optimized = DEFAULT_ROOT.with_name("gallery-webp")
    # The optimizer writes this catalog only after every WebP has been verified.
    return optimized if (optimized / "catalog.json").is_file() else DEFAULT_ROOT


def image_path(relative):
    root = library_root()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError("Reference must be an image inside the local gallery.")
    return path


def catalog_stamp():
    path = library_root() / "catalog.json"
    try:
        stat = path.stat()
        return (str(path), stat.st_mtime_ns, stat.st_size)
    except FileNotFoundError:
        return (str(path), 0, 0)


@lru_cache(maxsize=4)
def _read_catalog(stamp):
    if not stamp[1]:
        return {"pose": {}, "outfit": {}}
    document = json.loads(Path(stamp[0]).read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ValueError("Unsupported gallery catalog. Expected schema_version 1.")
    result = {"pose": {}, "outfit": {}}
    for kind in result:
        for entry in document.get(kind, []):
            identity = entry["id"]
            if not isinstance(identity, str) or not identity or identity == NONE or identity in result[kind]:
                raise ValueError(f"Invalid or duplicate {kind} id: {identity!r}")
            for field in ("label", "prompt", "category", "image", "thumbnail"):
                if not isinstance(entry.get(field), str):
                    raise ValueError(f"{identity}: {field} must be text.")
            image_path(entry["image"])
            image_path(entry["thumbnail"])
            result[kind][identity] = entry
    return result


def catalog():
    return _read_catalog(catalog_stamp())


def selected(kind, identity):
    if identity == NONE:
        return None
    entry = catalog()[kind].get(identity)
    if entry is None:
        raise ValueError(f"{kind.title()} '{identity}' is not in the local gallery. Install the assets or select another reference.")
    return entry


def choices(kind):
    return [NONE, *catalog()[kind]]
