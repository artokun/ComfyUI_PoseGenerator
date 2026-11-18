"""Read a local reference catalog. No downloads or code evaluation."""

import json
import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

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


def image_path(relative, root=None):
    root = library_root() if root is None else root
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError("Reference must be an image inside the local gallery.")
    return path


def file_stamp(path):
    try:
        stat = path.stat()
        return (str(path), stat.st_mtime_ns, stat.st_size)
    except FileNotFoundError:
        return (str(path), 0, 0)


def catalog_stamp(root=None):
    return file_stamp((library_root() if root is None else root) / "catalog.json")


@lru_cache(maxsize=64)
def _read_catalog(stamp):
    if not stamp[1]:
        return {"pose": {}, "outfit": {}}
    document = json.loads(Path(stamp[0]).read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ValueError("Unsupported gallery catalog. Expected schema_version 1.")
    result = {"pose": {}, "outfit": {}}
    root = Path(stamp[0]).parent
    for kind in result:
        for entry in document.get(kind, []):
            identity = entry["id"]
            if not isinstance(identity, str) or not identity or identity == NONE or identity in result[kind]:
                raise ValueError(f"Invalid or duplicate {kind} id: {identity!r}")
            for field in ("label", "prompt", "category", "image", "thumbnail"):
                if not isinstance(entry.get(field), str):
                    raise ValueError(f"{identity}: {field} must be text.")
            image_path(entry["image"], root)
            image_path(entry["thumbnail"], root)
            result[kind][identity] = entry
    return result


def catalog():
    return _read_catalog(catalog_stamp())


@lru_cache(maxsize=256)
def _studio_label(stamp):
    run = json.loads(Path(stamp[0]).read_text(encoding="utf-8"))
    if not run.get("regenerated_from"):
        return run.get("label") or Path(stamp[0]).parent.name


def gallery_sources():
    root = library_root()
    assets = DEFAULT_ROOT.parent
    standard = (DEFAULT_ROOT, DEFAULT_ROOT.with_name("gallery-webp"))
    name = "Original gallery" if root in standard else root.name
    yield "main", name, catalog_stamp(root)
    if root not in standard:
        return  # An explicitly configured external library stays independent.
    studio = assets / "gallery-studio"
    if not studio.is_symlink():
        for manifest in sorted(studio.glob("*/studio.json")):
            folder = manifest.parent
            if folder.is_symlink() or manifest.is_symlink() or (folder / "catalog.json").is_symlink():
                continue
            stamp = catalog_stamp(folder)
            if stamp[1]:
                label = _studio_label(file_stamp(manifest))
                if label:
                    yield "studio." + folder.name, label, stamp
    for path in sorted(assets.glob("*/catalog.json")):
        folder = path.parent
        if folder in (*standard, studio) or folder.is_symlink() or path.is_symlink():
            continue
        yield "folder." + quote(folder.name, safe=""), folder.name, file_stamp(path)


def _studio_pose_ids(root):
    # Captioning can rename the catalog ID and PNG. The generation receipt
    # keeps a stable ID for the same drawing across captions and retries.
    path = root / "batch.json"
    if not path.is_file() or path.is_symlink():
        return {}
    batch = json.loads(path.read_text(encoding="utf-8"))
    jobs = {**batch.get("jobs", {}), **batch.get("replacements", {})}
    return {job["output"]: job["id"] for job in jobs.values() if job.get("downloaded")}


@lru_cache(maxsize=2)
def _gallery_index(sources):
    galleries = []
    entries = {"pose": {}, "outfit": {}}
    for key, label, stamp in sources:
        root = Path(stamp[0]).parent
        local = _read_catalog(stamp)
        # The list feeds the Pose gallery picker: skip a missing original catalog and
        # galleries that only carry outfits.
        if (stamp[1] if key == "main" else local["pose"]):
            galleries.append({"id": key, "label": label})
        stable_ids = _studio_pose_ids(root) if key.startswith("studio.") else {}
        # Studio runs are pose sets; the original gallery and folder galleries may also carry outfits.
        for kind in (("pose",) if key.startswith("studio.") else ("pose", "outfit")):
            for local_id, entry in local[kind].items():
                identity = local_id
                if key != "main":
                    stable = stable_ids.get(entry["image"], local_id)
                    identity = f"@{key}:{quote(stable, safe='')}"
                    while identity in entries[kind]:
                        identity = "@" + identity
                entries[kind][identity] = {**entry, "id": identity, "catalog_id": local_id,
                                           "gallery": key, "_root": root}
    return galleries, entries


def gallery_index():
    return _gallery_index(tuple(gallery_sources()))


def selected(kind, identity):
    if identity == NONE:
        return None
    primary = catalog()[kind].get(identity)
    if primary is not None:
        return {**primary, "_root": library_root()}
    entry = gallery_index()[1][kind].get(identity)
    if entry is None:
        raise ValueError(f"{kind.title()} '{identity}' is not in the local gallery. Install the assets or select another reference.")
    return entry


def choices(kind):
    return [NONE, *gallery_index()[1][kind]]
