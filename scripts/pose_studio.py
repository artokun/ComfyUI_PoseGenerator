#!/usr/bin/env python3
"""Local browser UI for the resumable SpicyAPI pose generator."""

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path, PurePosixPath
import re
import threading
from urllib.parse import quote, urlsplit
import uuid
import webbrowser

from aiohttp import web

try:
    from . import generate_pose_references as generation, pose_metadata, pose_depth
except ImportError:
    import generate_pose_references as generation
    import pose_metadata
    import pose_depth

ROOT = generation.ROOT
UI = Path(__file__).with_name("pose_studio_web")
MAX_IMAGE = 10 * 1024 * 1024
MAX_FILES = 500
MAX_FOLDER = 1024 * 1024 * 1024
DEFAULT_STYLE = "spicy://f/fil_06gyh8s891hf037cmrt9970590"
STUDIO = web.AppKey("studio", object)


def child(root, name):
    """Treat browser-supplied filenames as relative POSIX paths, never OS paths."""
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise generation.BatchError("Invalid filename.")
    parts = name.split("/")
    if any(part in ("", ".", "..") or part.startswith(".") for part in parts):
        raise generation.BatchError("Hidden files and parent paths are not accepted.")
    path = root.joinpath(*PurePosixPath(name).parts).resolve()
    if not path.is_relative_to(root.resolve()):
        raise generation.BatchError("File is outside its folder.")
    return path


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise generation.BatchError("Unknown item.")
    return value


def read_json(path):
    return json.loads(path.read_text())


def folder_alias(outputs, run_id, label):
    """Provide a readable Finder path alongside stable internal run IDs."""
    directory = outputs / "by-name"
    directory.mkdir(exist_ok=True)
    base = re.sub(r"[^\w '()-]+", " ", label).strip() or "Pose folder"
    path = directory / base
    index = 1
    target = outputs / identifier(run_id)
    while path.exists() or path.is_symlink():
        if path.is_symlink() and path.resolve() == target.resolve():
            return path.name
        index += 1
        path = directory / f"{base} ({index})"
    path.symlink_to(Path("..") / run_id, target_is_directory=True)
    return path.name


def rename_saved_folder(outputs, run_id, label):
    if not isinstance(label, str) or not label.strip().isprintable() or not 1 <= len(label.strip()) <= 100:
        raise generation.BatchError("Use a folder name between 1 and 100 characters.")
    record_path = outputs / identifier(run_id) / "studio.json"
    run = read_json(record_path)
    if run.get("regenerated_from"):
        raise generation.BatchError("Rename the original folder.")
    alias = folder_alias(outputs, run_id, label.strip())
    previous = run.get("folder_alias")
    run.update(label=label.strip(), folder_alias=alias)
    generation.write_json(record_path, run)
    if previous and previous != alias:
        old = child(outputs, "by-name") / previous
        new = outputs / "by-name" / alias
        if old.is_symlink() and old.resolve() == (outputs / run_id).resolve():
            if old.lstat().st_ino != new.lstat().st_ino:
                old.unlink()
            else:
                old.rename(new)



def check_image(content):
    if not content or len(content) > MAX_IMAGE:
        raise generation.BatchError("Each image must be between 1 byte and 10 MiB.")
    try:
        mime, size = generation.image_details(content)
    except Exception as error:
        raise generation.BatchError("This file is not a readable PNG, JPEG, or WebP image.") from error
    if max(size) > 8192 or size[0] * size[1] > 8_000_000:
        raise generation.BatchError("Input images must be at most 8 megapixels and 8192 pixels on each side.")
    return mime, size


def root_run_id(outputs, run_id):
    seen = set()
    while run_id not in seen:
        seen.add(run_id)
        record = read_json(outputs / identifier(run_id) / "studio.json")
        parent = record.get("regenerated_from")
        if not parent:
            return run_id
        run_id = parent["id"]
    raise generation.BatchError("Invalid retry ancestry.")


def apply_replacement(outputs, retry_id):
    """Adopt a verified retry into its original batch, preserving prior versions."""
    retry_path = outputs / identifier(retry_id)
    retry = read_json(retry_path / "studio.json")
    if not retry.get("regenerated_from") or not (retry_path / "batch.json").is_file():
        return False
    name = retry["regenerated_from"]["name"]
    result = read_json(retry_path / "batch.json")["jobs"].get(name, {})
    if not result.get("downloaded"):
        return False
    content = child(retry_path, result["output"]).read_bytes()
    if generation.sha256(content) != result["output_sha256"]:
        raise generation.BatchError("Retry image failed verification; the previous result is preserved.")
    destination = outputs / root_run_id(outputs, retry_id)
    with generation.batch_lock(destination):
        parent = read_json(destination / "studio.json")
        state = read_json(destination / "batch.json")
        if name in state.get("trash", {}):
            return False
        original = state["jobs"].get(name)
        if (not original or original["source_sha256"] != result["source_sha256"]
                or parent["recipe"] != retry["recipe"] or parent["source_id"] != retry["source_id"]):
            raise generation.BatchError("Retry settings differ from the original image.")
        replacements = state.setdefault("replacements", {})
        current = replacements.get(name)
        version = (retry["created"], retry_id)
        if current and (current["retry_created"], current["retry_id"]) > version:
            return False  # Replaying an old retry must not undo a newer result.
        target = (current or original)["output"]
        if not current or current["retry_id"] != retry_id:
            previous = current or original
            previous_bytes = child(destination, previous["output"]).read_bytes()
            if generation.sha256(previous_bytes) != previous["output_sha256"]:
                raise generation.BatchError("Previous result failed verification; replacement stopped.")
            archive = f"history/{original['id']}/{previous.get('retry_id', 'original')}.png"
            generation.atomic_write(child(destination, archive), previous_bytes)
            if current:
                state.setdefault("replacement_history", {}).setdefault(name, []).append({**current, "output": archive})
            else:
                original["output"] = archive
            replacements[name] = {**result, "output": target, "retry_id": retry_id, "retry_created": retry["created"]}
            # Save recovery information before replacing the image. A crash can
            # then recover this exact retry, never resubmit the original task.
            generation.write_json(destination / "batch.json", state)
        output = child(destination, target)
        if not output.is_file() or generation.sha256(output.read_bytes()) != result["output_sha256"]:
            generation.atomic_write(output, content)
        generation.write_catalog(destination, state)
    return True


def finish_curation(destination, state):
    """Replay an interrupted file move and catalog update under the batch lock."""
    change = state.get("curation_change")
    if not change:
        return
    source, target = (child(destination, change[key]) for key in ("from", "to"))
    if source != target and source.exists():
        if target.exists():
            raise generation.BatchError("A different file already occupies this catalog name.")
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
    if not target.is_file() or generation.sha256(target.read_bytes()) != change["sha256"]:
        raise generation.BatchError("The generated file has changed. Catalog update stopped.")
    generation.write_catalog(destination, state, changed_source=change["source"])
    del state["curation_change"]
    generation.write_json(destination / "batch.json", state)


def save_curation(destination, state, name, old, new, digest):
    source, target = child(destination, old), child(destination, new)
    if not source.is_file() or generation.sha256(source.read_bytes()) != digest:
        raise generation.BatchError("The generated image is missing or has changed.")
    if target != source and target.exists():
        raise generation.BatchError("A different file already occupies this catalog name.")
    state["curation_change"] = {"source": name, "from": old, "to": new, "sha256": digest}
    generation.write_json(destination / "batch.json", state)
    finish_curation(destination, state)


def drawing_edit_version(digest, metadata, tag_version):
    return generation.sha256(json.dumps([digest, metadata, tag_version], sort_keys=True).encode())


class Studio:
    def __init__(self, root=ROOT, client_factory=None):
        self.root = root
        self.cache = root / ".cache/pose-studio"
        self.outputs = root / "assets/gallery-studio"
        self.client_factory = client_factory or (lambda: generation.SpicyClient(generation.read_key()))
        self.active = None
        self.task = None
        self.pause_requested = threading.Event()
        self.lock = threading.RLock()
        self.current = None
        self.phase = None
        self.closing = False
        self.metadata_tasks = {}
        self.metadata_status = {}
        self.caption_workers = {}
        self.depth_tasks = {}
        self.depth_status = {}
        self.outputs.mkdir(parents=True, exist_ok=True)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.tag_path = self.outputs / "tag-groups.json"
        self.tag_groups = pose_metadata.pose_tags.validate(read_json(self.tag_path)
                            if self.tag_path.exists() else pose_metadata.pose_tags.DEFAULT)
        self.queue_path = self.cache / "queue.json"
        saved_queue = read_json(self.queue_path) if self.queue_path.is_file() else {}
        self.queue = list(dict.fromkeys(saved_queue.get("ids", [])))
        self.queue_revision = saved_queue.get("revision", 0)
        self.queue_paused = bool(self.queue)  # Restart never starts paid work.
        # Never resubmit just because the server restarted. Resume is explicit.
        for path in self.outputs.glob("*/studio.json"):
            record = read_json(path)
            captions = record.get("caption_queue", [])
            if captions and captions[0].get("started"):
                interrupted = captions.pop(0)
                record.setdefault("caption_errors", {})[interrupted["name"]] = "Server stopped during captioning. Check the saved caption before retrying."
                generation.write_json(path, record)
            if record.get("pending_import"):
                self.finish_import(record)
            batch_path = path.parent / "batch.json"
            if batch_path.is_file() and read_json(batch_path).get("curation_change"):
                with generation.batch_lock(path.parent):
                    finish_curation(path.parent, read_json(batch_path))
            if record["status"] in ("running", "pausing"):
                record["status"] = "paused"
                record["error"] = "Server stopped. Resume to recover saved jobs and continue."
                generation.write_json(path, record)
        # Migrate older separate retry runs and repair interrupted replacements.
        for path in sorted(self.outputs.glob("*/studio.json"), key=lambda p: (read_json(p)["created"], p.parent.name)):
            record = read_json(path)
            if record.get("regenerated_from"):
                try:
                    apply_replacement(self.outputs, record["id"])
                except (generation.BatchError, OSError):
                    self.update(record["id"], status="error", error="Could not install the retry. Resume it to recover; the previous image is preserved.")
        self.queue = [identity for identity in self.queue if self.run(identity)["status"] != "completed"]
        self.queue_paused = bool(self.queue)
        self.save_queue()

    def save_queue(self):
        with self.lock:
            self.queue_revision += 1
            generation.write_json(self.queue_path, {"ids": self.queue, "paused": self.queue_paused,
                                                    "revision": self.queue_revision})

    def source_path(self, source_id):
        return self.cache / "sources" / (identifier(source_id) + ".json")

    def source(self, source_id):
        path = self.source_path(source_id)
        if not path.is_file():
            raise generation.BatchError("Source folder was not found.")
        return read_json(path)

    def new_source(self, label):
        identity = uuid.uuid4().hex
        record = {"id": identity, "label": str(label or "Uploaded folder")[:100],
                  "directory": str(self.cache / "uploads" / identity), "files": [], "bytes": 0, "sealed": False}
        generation.write_json(self.source_path(identity), record)
        return record

    def add_image(self, source_id, name, content):
        record = self.source(source_id)
        if record["sealed"]:
            raise generation.BatchError("This folder belongs to a saved run. Upload changes as a new folder.")
        path = child(Path(record["directory"]), name)
        if path.suffix.lower() not in generation.EXTENSIONS:
            raise generation.BatchError("Only PNG, JPEG, and WebP images are accepted.")
        if name in record["files"]:
            raise generation.BatchError("Duplicate image filename.")
        if len(record["files"]) >= MAX_FILES or record["bytes"] + len(content) > MAX_FOLDER:
            raise generation.BatchError("Folder limit: 500 images or 1 GiB.")
        check_image(content)
        generation.atomic_write(path, content)
        record["files"].append(name)
        record["bytes"] += len(content)
        generation.write_json(self.source_path(source_id), record)

    def staged(self):
        directory = self.root / ".cache/pose-sources/staged_v2"
        files = sorted(p.name for p in directory.glob("*") if p.is_file() and not p.is_symlink()
                       and p.suffix.lower() in generation.EXTENSIONS)
        if not files:
            raise generation.BatchError("The staged folder is not available. Choose a folder instead.")
        record = self.new_source("Staged v2")
        record.update(directory=str(directory), files=files, sealed=True)
        generation.write_json(self.source_path(record["id"]), record)
        return record

    def public_source(self, record):
        return {"id": record["id"], "label": record["label"], "files": [
            {"name": name, "url": f"/media/source/{record['id']}?name={quote(name, safe='')}"}
            for name in sorted(record["files"])]}

    def style_path(self, style_id):
        path = self.cache / "styles" / (identifier(style_id) + ".image")
        if not path.is_file():
            raise generation.BatchError("Style image was not found. Upload it again.")
        return path

    def run_path(self, run_id):
        return self.outputs / identifier(run_id)

    def run(self, run_id):
        path = self.run_path(run_id) / "studio.json"
        if not path.is_file():
            raise generation.BatchError("Run was not found.")
        return read_json(path)

    def update(self, run_id, **changes):
        with self.lock:
            record = self.run(run_id)
            record.update(changes)
            generation.write_json(self.run_path(run_id) / "studio.json", record)

    def rename_folder(self, run_id, label):
        with self.lock:
            rename_saved_folder(self.outputs, run_id, label)
            self.save_queue()

    def source_file(self, run, name):
        added = run.get("added_files", {}).get(name)
        source_id = added["source_id"] if added else run["source_id"]
        source_name = added["name"] if added else name
        return child(Path(self.source(source_id)["directory"]), source_name)

    def create_run(self, body):
        source = self.source(body.get("source_id"))
        selected = body.get("files")
        if not isinstance(selected, list) or not selected or any(not isinstance(x, str) for x in selected):
            raise generation.BatchError("Select at least one source image.")
        if len(set(selected)) != len(selected) or not set(selected).issubset(source["files"]):
            raise generation.BatchError("Selection contains unknown or duplicate images.")
        prompt = body.get("prompt", "")
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 5000:
            raise generation.BatchError("Prompt must contain 1–5000 characters.")
        resolution = body.get("resolution", "2k")
        order = body.get("image_order", "source-first")
        if resolution not in ("1k", "1.5k", "2k") or order not in ("source-first", "style-first"):
            raise generation.BatchError("Unsupported generation settings.")
        per_image, total = generation.money(body.get("per_image")), generation.money(body.get("total"))
        if per_image <= 0 or total < per_image:
            raise generation.BatchError("Total cap must cover at least one image; caps must be positive.")
        style_id = body.get("style_id")
        if style_id:
            style = "sha256:" + generation.sha256(self.style_path(style_id).read_bytes())
        else:
            style = body.get("style_uri", "")
            if not isinstance(style, str) or not re.fullmatch(r"spicy://(?:f/fil_|a/ast_)[A-Za-z0-9_-]+", style):
                raise generation.BatchError("Upload a style image or enter a valid spicy:// reference.")
        recipe = {"model": generation.MODEL, "style": style, "prompt": prompt.strip(),
                  "resolution": resolution, "output_format": "png", "background": "opaque",
                  "aspect_ratio": "nearest-source"}
        if order == "source-first":
            recipe["image_order"] = order
        identity = uuid.uuid4().hex
        record = {"id": identity, "label": str(body.get("label") or source["label"])[:100],
                  "created": datetime.now(timezone.utc).isoformat(), "source_id": source["id"],
                  "files": selected, "recipe": recipe, "style_id": style_id,
                  "per_image": str(per_image), "total": str(total), "status": "paused", "error": None}
        source["sealed"] = True
        record["folder_alias"] = folder_alias(self.outputs, identity, record["label"])
        generation.write_json(self.source_path(source["id"]), source)
        generation.write_json(self.run_path(identity) / "studio.json", record)
        return identity

    def summary(self, run_id, detail=True):
        with self.lock:
            run = self.run(run_id)
            batch_path = self.run_path(run_id) / "batch.json"
            batch = read_json(batch_path) if batch_path.is_file() else {"jobs": {}}
            jobs = batch["jobs"]
            displayed = {**jobs, **batch.get("replacements", {})}
            trash = batch.get("trash", {})
            reserved = sum((generation.money(j["max_charge"]) for j in jobs.values()), Decimal(0))
            settled = sum((generation.money(j.get("cost", "0")) for j in jobs.values() if j.get("settled")), Decimal(0))
            result = {key: run[key] for key in ("id", "label", "created", "status", "error", "per_image", "total")}
            result.update(reserved=str(reserved), settled=str(settled), count=len(run["files"]) - len(trash),
                          completed=sum(bool(j.get("downloaded")) for name, j in jobs.items() if name not in trash),
                          destination=str(self.outputs / "by-name" / run["folder_alias"]
                                          if run.get("folder_alias") else self.run_path(run_id)))
            result["regenerated_from"] = run.get("regenerated_from")
            result["control_run_id"] = run_id
            result.update(queue_revision=self.queue_revision, queue_paused=self.queue_paused, queued_retries=0)
            latest = {}
            total_cap = generation.money(run["total"])
            if not run.get("regenerated_from"):
                attempts = [read_json(p) for p in self.outputs.glob("*/studio.json")]
                for attempt in sorted(attempts, key=lambda r: (r["created"], r["id"])):
                    relation = attempt.get("regenerated_from")
                    if not relation or root_run_id(self.outputs, attempt["id"]) != run_id:
                        continue
                    path = self.run_path(attempt["id"]) / "batch.json"
                    retry_jobs = read_json(path)["jobs"] if path.is_file() else {}
                    reserved += sum((generation.money(j["max_charge"]) for j in retry_jobs.values()), Decimal(0))
                    settled += sum((generation.money(j.get("cost", "0")) for j in retry_jobs.values() if j.get("settled")), Decimal(0))
                    total_cap += generation.money(attempt["total"])
                    latest[relation["name"]] = {**attempt, "job": retry_jobs.get(relation["name"], {})}
                for name, attempt in latest.items():
                    if name in trash:
                        continue
                    adopted = displayed.get(name, {}).get("retry_id") == attempt["id"]
                    if adopted:
                        continue
                    if self.active == attempt["id"]:
                        result.update(status="pausing" if attempt["status"] == "pausing" else "regenerating",
                                      control_run_id=attempt["id"], error=None)
                    elif self.active is None and attempt["status"] in ("error", "paused"):
                        result.update(status=attempt["status"], control_run_id=attempt["id"], error=attempt.get("error"),
                                      retry_failed=attempt["job"].get("state") in {"failed", "canceled", "expired"})
                result.update(reserved=str(reserved), settled=str(settled), total=str(total_cap))
            related_queue = [identity for identity in self.queue
                             if root_run_id(self.outputs, identity) == run_id and self.run(identity)["status"] != "completed"]
            result["queued_retries"] = sum(identity != self.active and bool(self.run(identity).get("regenerated_from"))
                                            for identity in related_queue)
            if self.active in related_queue:
                result.update(status="pausing" if self.queue_paused else "running" if self.active == run_id else "regenerating",
                              control_run_id=self.active, error=None)
            elif related_queue:
                result.update(status="paused" if self.queue_paused else "queued", control_run_id=related_queue[0],
                              error=self.run(related_queue[0]).get("error") if self.queue_paused else None)
            if not detail:
                return result
            result.update(recipe=run["recipe"], source_id=run["source_id"], style_id=run["style_id"], files=[])
            result["caption_prompt"] = run.get("caption_prompt", pose_metadata.DEFAULT_PROMPT)
            result["caption_prompt_sha256"] = pose_metadata.prompt_digest(result["caption_prompt"])
            result["tag_version"] = pose_metadata.pose_tags.version(self.tag_groups)
            result["tag_groups"] = self.tag_groups
            result["last_deleted"] = max(trash, key=lambda name: trash[name]["deleted_at"], default=None)
            result["deleted_count"] = len(trash)
            result["metadata_status"] = self.metadata_status.get(run_id, {})
            result["depth_status"] = self.depth_status.get(run_id, {})
            captions = run.get("caption_queue", [])
            result["caption_active"] = captions[0]["name"] if captions and captions[0].get("started") else None
            result["caption_queue"] = [item["name"] for item in captions if not item.get("started")]
            result["caption_errors"] = run.get("caption_errors", {})
            result["captions_paused"] = bool(captions and not self.caption_worker_active(run_id))
            result["generation_busy"] = bool(related_queue or (self.active and root_run_id(self.outputs, self.active) == run_id))
            for index, name in enumerate(run["files"]):
                if name in trash:
                    continue
                job = displayed.get(name, {})
                status = "saved" if job.get("downloaded") else job.get("state", "submitted" if job.get("task_id") else "waiting")
                if self.active == run_id and name == self.current and status != "saved":
                    status = self.phase or status
                if status == "succeeded":
                    status = "saving"
                attempt = latest.get(name)
                pending = bool(attempt and job.get("retry_id") != attempt["id"])
                waiting = [identity for identity in self.queue if identity != self.active]
                queue_position = waiting.index(attempt["id"]) + 1 if pending and attempt["id"] in waiting else None
                if pending:
                    status = ("regenerating" if self.active == attempt["id"] else "retry-queued" if queue_position
                              else "retry-error" if attempt["status"] == "error" else "retry-paused")
                version = job.get("retry_id", "original")
                added = run.get("added_files", {}).get(name, {})
                source_id = added.get("source_id", run["source_id"])
                source_name = added.get("name", name)
                metadata = generation.current_metadata(batch, name, job)
                if metadata and "tag_groups" in metadata:
                    metadata = {**metadata, **generation.visible_tags(metadata, self.tag_groups)}
                result["files"].append({"name": name, "state": status,
                    "has_depth": bool(job.get("downloaded") and (batch_path.parent / "depth" / (job["output_sha256"] + ".png")).is_file()),
                    "metadata": metadata,
                    "edit_version": drawing_edit_version(job.get("output_sha256"), batch.get("metadata", {}).get(name), result["tag_version"]),
                    "imported": added.get("kind") == "drawing",
                    "source_url": None if added.get("kind") == "drawing" else f"/media/source/{source_id}?name={quote(source_name, safe='')}",
                    "output_url": f"/media/result/{run_id}/{index}?v={version}" if job.get("downloaded") else None,
                    "retry_pending": pending and attempt["job"].get("state") not in {"failed", "canceled", "expired"},
                    "queue_position": queue_position, "queue_paused": self.queue_paused,
                    "retry_error": attempt.get("error") if pending else None})
            return result

    def curation_folder(self, run_id, *, caption_worker=False):
        run = self.run(run_id)
        if run.get("regenerated_from"):
            raise generation.BatchError("Open the original folder to curate its images.")
        if any(root_run_id(self.outputs, identity) == run_id for identity in [*self.queue, self.active] if identity):
            raise generation.BatchError("Finish this folder's generation queue before curating it.")
        if not caption_worker and (self.metadata_status.get(run_id, {}).get("running") or self.caption_worker_active(run_id)):
            raise generation.BatchError("This folder's metadata pass is running.")
        destination = self.run_path(run_id)
        if not (destination / "batch.json").is_file():
            raise generation.BatchError("This folder has no generated drawings yet.")
        return run, destination

    def set_deleted(self, run_id, name, deleted):
        run, destination = self.curation_folder(run_id)
        if not isinstance(name, str) or name not in run["files"]:
            raise generation.BatchError("Unknown drawing.")
        with generation.batch_lock(destination):
            state = read_json(destination / "batch.json")
            finish_curation(destination, state)
            trash = state.setdefault("trash", {})
            if deleted == (name in trash):
                return
            job = state.get("replacements", {}).get(name) or state["jobs"].get(name, {})
            if not job.get("downloaded"):
                raise generation.BatchError("Only finished drawings can be deleted.")
            active = job["output"]
            archived = f"trash/{job['id']}.png"
            if deleted:
                trash[name] = {"deleted_at": datetime.now(timezone.utc).isoformat()}
            else:
                del trash[name]
            save_curation(destination, state, name, active if deleted else archived,
                          archived if deleted else active, job["output_sha256"])
        self.save_queue()

    def save_caption_prompt(self, run_id, value):
        if self.run(run_id).get("regenerated_from"):
            raise generation.BatchError("Open the original folder to edit its caption prompt.")
        prompt = pose_metadata.normalize_prompt(value)
        self.update(run_id, caption_prompt=prompt)
        self.save_queue()

    def edit_drawing(self, run_id, body):
        run, destination = self.curation_folder(run_id)
        name = body.get("name")
        if not isinstance(name, str) or name not in run["files"]:
            raise generation.BatchError("Unknown drawing.")
        label = body.get("label")
        if not isinstance(label, str) or len(label) > 150:
            raise generation.BatchError("Use a caption of up to 150 characters.")
        label = " ".join(label.split())
        if not label.isprintable():
            raise generation.BatchError("Use printable text for the caption.")
        with generation.batch_lock(destination):
            state = read_json(destination / "batch.json")
            finish_curation(destination, state)
            job = state.get("replacements", {}).get(name) or state["jobs"].get(name, {})
            if name in state.get("trash", {}) or not job.get("downloaded"):
                raise generation.BatchError("Only finished drawings in this folder can be edited.")
            digest = job["output_sha256"]
            metadata = state.setdefault("metadata", {})
            version = drawing_edit_version(digest, metadata.get(name), pose_metadata.pose_tags.version(self.tag_groups))
            if body.get("version") != version:
                raise generation.BatchError("The drawing or its tags changed. Reload the saved values before editing.")
            groups = self.tag_groups
            selected = pose_metadata.pose_tags.select(body.get("tag_groups"), groups)
            current = generation.current_metadata(state, name, job) or {}
            record = metadata[name] if current else {"sha256": digest}
            edits = dict(record.get("edits", {}))
            if label != current.get("label", ""):
                edits["label"] = label
            visible = generation.visible_tags({"tag_groups": current.get("tag_groups", {})}, groups)["tag_groups"]
            changes = {row: values for row, values in selected.items() if values != visible[row]}
            if changes:
                edits["tag_groups"] = {**edits.get("tag_groups", {}), **changes}
            if edits == record.get("edits", {}):
                return
            metadata[name] = {**record, "edits": edits}
            save_curation(destination, state, name, job["output"], job["output"], digest)
        self.save_queue()

    def add_to_folder(self, run_id, source_id, kind):
        run = self.run(run_id)
        stage = self.source(source_id)
        if source_id in run.get("added_sets", []):
            if run.get("pending_import"):
                self.finish_import(run)
            return
        run, dest = self.curation_folder(run_id)
        if kind not in ("photo", "drawing") or not stage["files"] or stage["sealed"]:
            raise generation.BatchError("Choose a fresh set of photos or finished drawings.")
        if len(run["files"]) + len(stage["files"]) > MAX_FILES:
            raise generation.BatchError("A Studio folder can contain up to 500 images.")
        state = read_json(dest / "batch.json")
        finished = all(state["jobs"].get(name, {}).get("downloaded") for name in run["files"])
        added = run.setdefault("added_files", {})
        new_names = []
        for original in stage["files"]:
            path = child(Path(stage["directory"]), original)
            if kind == "drawing" and generation.image_details(path.read_bytes())[0] != "image/png":
                raise generation.BatchError("Choose PNGs for finished mannequin drawings.")
            name = original
            suffix = 1
            while name in run["files"]:
                suffix += 1
                name = str(Path(original).with_name(f"{Path(original).stem} ({suffix}){Path(original).suffix}"))
            added[name] = {"source_id": source_id, "name": original, "kind": kind}
            run["files"].append(name)
            new_names.append(name)
        run.setdefault("added_sets", []).append(source_id)
        if kind == "photo":
            run.update(total=str(generation.money(run["total"]) + len(new_names) * generation.money(run["per_image"])),
                       status="paused", error=None)
        else:
            run["pending_import"] = new_names
        generation.write_json(dest / "studio.json", run)
        stage["sealed"] = True
        generation.write_json(self.source_path(source_id), stage)
        if kind == "drawing":
            self.finish_import(run)
        elif finished:
            self.enqueue(run_id)
        self.save_queue()

    def finish_import(self, run):
        dest = self.run_path(run["id"])
        with generation.batch_lock(dest):
            state = read_json(dest / "batch.json")
            for name in run["pending_import"]:
                data = self.source_file(run, name).read_bytes()
                digest = generation.sha256(data)
                identity = "pose-" + generation.sha256(name.encode())[:16]
                relative = f"img/{identity}.png"
                generation.atomic_write(child(dest, relative), data)
                state["jobs"][name] = {"id": identity, "output": relative, "source_sha256": digest,
                    "output_sha256": digest, "downloaded": True, "state": "imported", "max_charge": "0"}
            generation.write_json(dest / "batch.json", state)
            generation.write_catalog(dest, state)
            del run["pending_import"]
            generation.write_json(dest / "studio.json", run)

    def caption_worker_active(self, run_id):
        task = self.caption_workers.get(run_id)
        return task is not None and not task.done()

    def start_depth(self, run_id, limit):
        if limit not in (None, 1) or self.closing:
            raise generation.BatchError("Invalid depth request.")
        if self.depth_status.get(run_id, {}).get("running"):
            raise generation.BatchError("This gallery already has a depth pass running.")
        run, folder = self.curation_folder(run_id)
        url = generation.read_key(("POSE_DEPTH_COMFY_URL",))
        batch = read_json(folder / "batch.json")
        jobs = {**batch["jobs"], **batch.get("replacements", {})}
        pending = [(name, jobs[name]["output_sha256"]) for name in run["files"]
                   if name not in batch.get("trash", {}) and jobs.get(name, {}).get("downloaded")
                   and not (folder / "depth" / (jobs[name]["output_sha256"] + ".png")).is_file()]
        if limit:
            pending = pending[:limit]
        if not pending:
            raise generation.BatchError("All finished drawings already have depth maps.")
        status = {"running": True, "done": 0, "total": len(pending), "error": None}
        self.depth_status[run_id] = status
        self.depth_tasks[run_id] = asyncio.create_task(self.depth_pass(folder, pending, url, status))
        self.save_queue()

    async def depth_pass(self, folder, pending, url, status):
        try:
            for name, digest in pending:
                if self.closing:
                    break
                state = read_json(folder / "batch.json")
                job = state.get("replacements", {}).get(name) or state["jobs"][name]
                if name in state.get("trash", {}) or job["output_sha256"] != digest:
                    continue
                content = child(folder, job["output"]).read_bytes()
                if generation.sha256(content) != digest:
                    raise generation.BatchError("The drawing changed on disk.")
                await asyncio.to_thread(pose_depth.generate, content, digest, folder / "depth", url)
                status["done"] += 1
                self.save_queue()
        except generation.BatchError as error:
            status["error"] = str(error)
        except Exception:
            status["error"] = "Depth request failed. Check the ComfyUI server address and execution history."
        finally:
            status["running"] = False
            self.save_queue()

    def enqueue_caption(self, run_id, body):
        run = self.run(run_id)
        name = body.get("name")
        if not isinstance(name, str) or name not in run["files"]:
            raise generation.BatchError("Choose one drawing to caption.")
        request_id = identifier(body["request_id"]) if "request_id" in body else uuid.uuid4().hex
        receipts = run.get("caption_requests", {})
        if request_id in receipts:
            if receipts[request_id] != name:
                raise generation.BatchError("This caption request belongs to another drawing.")
            return
        active = self.caption_worker_active(run_id)
        _, dest = self.curation_folder(run_id, caption_worker=active)
        if self.closing:
            raise generation.BatchError("The server is stopping.")
        queue = run.get("caption_queue", [])
        errors = run.get("caption_errors", {})
        if not any(item["name"] == name for item in queue):
            provider = body.get("provider")
            pose_metadata.api_key(provider)
            prompt = pose_metadata.normalize_prompt(body.get("caption_prompt", run.get("caption_prompt", pose_metadata.DEFAULT_PROMPT)))
            batch = read_json(dest / "batch.json")
            job = batch.get("replacements", {}).get(name) or batch["jobs"].get(name, {})
            if name in batch.get("trash", {}) or not job.get("downloaded"):
                raise generation.BatchError("Only finished drawings can be captioned.")
            queue.append({"name": name, "provider": provider, "prompt": prompt, "digest": job["output_sha256"]})
            errors.pop(name, None)
        receipts[request_id] = name
        self.update(run_id, caption_queue=queue, caption_requests=receipts, caption_errors=errors)
        self.resume_captions(run_id)
        self.save_queue()

    def resume_captions(self, run_id):
        if self.caption_worker_active(run_id):
            return
        self.curation_folder(run_id)
        if self.closing or not self.run(run_id).get("caption_queue"):
            raise generation.BatchError("There are no queued captions to resume.")
        self.caption_workers[run_id] = asyncio.create_task(self.drain_captions(run_id))

    async def drain_captions(self, run_id):
        while not self.closing:
            queue = self.run(run_id).get("caption_queue", [])
            if not queue:
                return
            item = queue[0]
            item["started"] = True
            self.update(run_id, caption_queue=queue)
            self.save_queue()
            error = None
            try:
                self.start_metadata(run_id, item["provider"], caption_prompt=item["prompt"],
                                    target=item["name"], expected_digest=item["digest"])
                await self.metadata_tasks[run_id]
                error = self.metadata_status[run_id].get("error")
            except generation.BatchError as exc:
                error = str(exc)
            except Exception:
                error = "Could not caption this drawing."
            run = self.run(run_id)  # Include additional clicks received during inference.
            errors = run.get("caption_errors", {})
            if error:
                errors[item["name"]] = error
            self.update(run_id, caption_queue=run["caption_queue"][1:], caption_errors=errors)
            self.save_queue()

    def start_metadata(self, run_id, provider, limit=None, caption_prompt=None, *, target=None, expected_digest=None):
        run, destination = self.curation_folder(run_id, caption_worker=expected_digest is not None)
        if self.closing or provider not in pose_metadata.MODELS or limit not in (None, 1):
            raise generation.BatchError("Invalid metadata request.")
        if target is not None and (not isinstance(target, str) or target not in run["files"]):
            raise generation.BatchError("Unknown drawing.")
        key = pose_metadata.api_key(provider)
        prompt = pose_metadata.normalize_prompt(run.get("caption_prompt", pose_metadata.DEFAULT_PROMPT)
                                               if caption_prompt is None else caption_prompt)
        prompt_hash = pose_metadata.prompt_digest(prompt)
        model = pose_metadata.MODELS[provider]
        groups = self.tag_groups
        tag_version = pose_metadata.pose_tags.version(groups)
        with generation.batch_lock(destination):
            state = read_json(destination / "batch.json")
            finish_curation(destination, state)
            jobs = {**state["jobs"], **state.get("replacements", {})}
            pending = []
            for name in [target] if target is not None else run["files"]:
                job = jobs.get(name, {})
                if name in state.get("trash", {}) or not job.get("downloaded"):
                    continue
                if expected_digest is not None and job["output_sha256"] != expected_digest:
                    raise generation.BatchError("The drawing changed while its caption was queued.")
                metadata = generation.current_metadata(state, name, job) or {}
                if (target is None and metadata.get("model") == model and metadata.get("caption_prompt_sha256") == prompt_hash
                        and metadata.get("tag_version") == tag_version):
                    continue
                pending.append((name, job))
                if limit == 1:
                    break
        if not pending:
            if target is not None:
                raise generation.BatchError("Only finished drawings in this folder can be captioned.")
            raise generation.BatchError("All finished drawings already have metadata for this prompt and model.")
        if run.get("caption_prompt") != prompt:
            self.update(run_id, caption_prompt=prompt)
        # Reserve numbers once per folder so separate generated catalogs can be combined.
        numbers = run.get("pose_numbers", {})
        if len(numbers) < len(run["files"]):
            others = (read_json(p) for p in self.outputs.glob("*/studio.json"))
            number = max((n for r in others for n in r.get("pose_numbers", {}).values()), default=0)
            for name in run["files"]:
                if name not in numbers:
                    number += 1
                    numbers[name] = number
            self.update(run_id, pose_numbers=numbers)
        status = {"running": True, "total": len(pending), "done": 0, "error": None, "provider": provider, "target": target}
        self.metadata_status[run_id] = status
        self.metadata_tasks[run_id] = asyncio.create_task(self.describe_folder(
            run_id, pending, numbers, provider, key, prompt, groups, status))
        self.save_queue()

    async def describe_folder(self, run_id, pending, numbers, provider, key, prompt, groups, status):
        destination = self.run_path(run_id)
        prompt_hash = pose_metadata.prompt_digest(prompt)
        try:
            for name, job in pending:
                if self.closing:
                    break
                content = child(destination, job["output"]).read_bytes()
                if generation.sha256(content) != job["output_sha256"]:
                    raise generation.BatchError("The drawing has changed on disk.")
                raw = await asyncio.to_thread(pose_metadata.describe, content, provider, key, prompt, groups)
                fields = pose_metadata.catalog_fields(raw, numbers[name], groups)
                with generation.batch_lock(destination):
                    state = read_json(destination / "batch.json")
                    current = state.get("replacements", {}).get(name) or state["jobs"][name]
                    if current["output_sha256"] != job["output_sha256"] or name in state.get("trash", {}):
                        raise generation.BatchError("The drawing changed during the metadata pass.")
                    previous = generation.current_metadata(state, name, current) or {}
                    edits = dict(previous.get("edits", {}))
                    if status["target"] == name and fields.get("review") and previous.get("label"):
                        raise generation.BatchError("Model could not classify this drawing. The saved caption was kept.")
                    if status["target"] == name and not fields.get("review"):
                        edits.pop("label", None)
                    old = current["output"]
                    new = f"img/{fields.get('id', current['id'])}.png"
                    current["output"] = new
                    state.setdefault("caption_prompts", {})[prompt_hash] = prompt
                    state.setdefault("metadata", {})[name] = {
                        **fields, "sha256": current["output_sha256"], "model": pose_metadata.MODELS[provider],
                        "caption_prompt_sha256": prompt_hash, "tag_version": pose_metadata.pose_tags.version(groups)}
                    if edits:
                        state["metadata"][name]["edits"] = edits
                    save_curation(destination, state, name, old, new, current["output_sha256"])
                status["done"] += 1
                status["last_label"] = edits.get("label", fields.get("label", "Needs pose review"))
                self.save_queue()
        except generation.BatchError as error:
            status["error"] = f"{name} · {error}"
        except Exception:
            status["error"] = "Could not save the metadata. Completed drawings are saved."
        finally:
            status["running"] = False
            self.save_queue()

    def regenerate(self, run_id, name, request_id):
        """Create an internal one-image attempt, adopted into the original batch."""
        identity = identifier(request_id)
        parent = self.run(run_id)
        if not isinstance(name, str) or name not in parent["files"]:
            raise generation.BatchError("This image is not in the selected run.")
        batch_path = self.run_path(run_id) / "batch.json"
        if batch_path.is_file() and name in read_json(batch_path).get("trash", {}):
            raise generation.BatchError("Restore the deleted drawing before regenerating it.")
        root_id = root_run_id(self.outputs, run_id)
        if self.metadata_status.get(root_id, {}).get("running") or self.caption_worker_active(root_id):
            raise generation.BatchError("Wait for this folder's metadata pass before regenerating.")
        relation = {"id": run_id, "name": name, "label": parent["label"]}
        if (self.run_path(identity) / "studio.json").exists():
            if self.run(identity).get("regenerated_from") != relation:
                raise generation.BatchError("This retry identifier belongs to a different image.")
            return identity  # Repeated HTTP requests recover the same paid attempt.

        # An interrupted regeneration should be recovered before creating another
        # paid job, including when the browser has lost its request identifier.
        for path in self.outputs.glob("*/studio.json"):
            previous = read_json(path)
            if identity in previous.get("regeneration_requests", []):
                if previous.get("regenerated_from") != relation:
                    raise generation.BatchError("This retry identifier belongs to a different image.")
                return previous["id"]
            if previous.get("regenerated_from") != relation or previous["status"] == "completed":
                continue
            batch_path = path.parent / "batch.json"
            jobs = read_json(batch_path)["jobs"] if batch_path.is_file() else {}
            job = jobs.get(name, {})
            if ((not job.get("downloaded") and job.get("state") not in {"failed", "canceled", "expired"})
                    or (job.get("downloaded") and previous["status"] == "error")):
                self.update(previous["id"], regeneration_requests=previous.get("regeneration_requests", []) + [identity])
                return previous["id"]

        if self.closing:
            raise generation.BatchError("The server is stopping. Restart it before adding more work.")
        batch_path = self.run_path(run_id) / "batch.json"
        batch = read_json(batch_path) if batch_path.is_file() else {"jobs": {}}
        job = batch.get("replacements", {}).get(name) or batch["jobs"].get(name, {})
        if not job.get("downloaded"):
            raise generation.BatchError("Wait for this image to finish before regenerating it.")
        output = child(self.run_path(run_id), job["output"])
        if not output.is_file() or generation.sha256(output.read_bytes()) != job["output_sha256"]:
            raise generation.BatchError("The saved result is missing or changed. Resume its run to recover it first.")
        if parent.get("added_files", {}).get(name, {}).get("kind") == "drawing":
            raise generation.BatchError("This imported drawing has no generation source.")
        source = self.source_file(parent, name)
        if generation.sha256(source.read_bytes()) != job["source_sha256"]:
            raise generation.BatchError("The original source image has changed. Upload it as a new folder.")
        if parent["style_id"]:
            style = self.style_path(parent["style_id"]).read_bytes()
            if "sha256:" + generation.sha256(style) != parent["recipe"]["style"]:
                raise generation.BatchError("The original style image has changed. Upload it in a new run.")
        record = {"id": identity, "label": (parent["label"] + " · retry")[:100],
                  "created": datetime.now(timezone.utc).isoformat(),
                  "source_id": parent["source_id"], "files": [name],
                  "recipe": parent["recipe"], "style_id": parent["style_id"],
                  "per_image": parent["per_image"], "total": parent["per_image"],
                  "status": "paused", "error": None, "regenerated_from": relation,
                  "regeneration_requests": [identity]}
        generation.write_json(self.run_path(identity) / "studio.json", record)
        return identity

    def available(self):
        if self.closing or self.active:
            raise generation.BatchError("Another run is active. Pause it before starting a different run.")

    def start(self, run_id):
        self.available()
        with self.lock:
            self.queue_paused = False
        self.enqueue(run_id)

    def enqueue(self, run_id):
        """Persist each user-requested attempt once, in click order."""
        if self.closing:
            raise generation.BatchError("The server is stopping. Restart it before adding more work.")
        root_id = root_run_id(self.outputs, run_id)
        if self.metadata_status.get(root_id, {}).get("running") or self.caption_worker_active(root_id):
            raise generation.BatchError("Wait for this folder's metadata pass before starting generation.")
        relation = self.run(run_id).get("regenerated_from")
        if relation and relation["name"] in read_json(self.run_path(root_id) / "batch.json").get("trash", {}):
            raise generation.BatchError("Restore the deleted drawing before resuming its retry.")
        with self.lock:
            if self.run(run_id)["status"] == "completed" or run_id == self.active:
                return
            if run_id not in self.queue:
                self.queue.append(run_id)
                self.update(run_id, status="queued", error=None)
            self.save_queue()
        if self.active is None and not self.queue_paused and (self.task is None or self.task.done()):
            self.task = asyncio.create_task(self.drain_queue())

    async def drain_queue(self):
        while True:
            with self.lock:
                if self.closing or self.queue_paused or not self.queue:
                    return
                run_id = self.queue[0]
                try:
                    client = self.client_factory()
                except Exception:
                    self.queue_paused = True
                    self.update(run_id, status="error", error="API key is unavailable. Fix the server configuration, then resume the queue.")
                    self.save_queue()
                    return
                self.active = run_id
                self.pause_requested.clear()
                self.current = self.phase = None
                self.update(run_id, status="running", error=None)
                self.save_queue()
            await asyncio.to_thread(self.work, run_id, client)
            with self.lock:
                # Errors retain their receipts and old image, but do not prevent
                # other independently requested images from finishing.
                if self.run(run_id)["status"] != "paused":
                    self.queue.remove(run_id)
                self.save_queue()

    def pause(self, run_id):
        with self.lock:
            if self.active != run_id and run_id not in self.queue:
                raise generation.BatchError("This run is not in the active queue.")
            self.queue_paused = True
            self.pause_requested.set()
            if self.active:
                self.update(self.active, status="pausing")
            self.save_queue()

    def work(self, run_id, client):
        studio = self

        class ProgressBatch(generation.Batch):
            def save(self):
                with studio.lock:
                    super().save()
                    job = self.state["jobs"].get(studio.current, {})
                    studio.phase = "saved" if job.get("downloaded") else job.get("state", "submitted" if job.get("task_id") else "preparing")

        try:
            record = self.run(run_id)
            source = Path(self.source(record["source_id"])["directory"])
            style = self.style_path(record["style_id"]).read_bytes() if record["style_id"] else None
            with generation.batch_lock(self.run_path(run_id)):
                batch = ProgressBatch(source, self.run_path(run_id), record["recipe"], client,
                                      record["per_image"], record["total"], 600)
                parent = self.run(root_run_id(self.outputs, run_id)) if record.get("regenerated_from") else record
                for name in record["files"]:
                    if self.pause_requested.is_set():
                        break
                    with self.lock:
                        self.current, self.phase = name, "preparing"
                    if parent.get("added_files", {}).get(name, {}).get("kind") == "drawing":
                        continue
                    batch.process(self.source_file(parent, name), style, relative=name)
                batch.catalog()
                complete = all(batch.state["jobs"].get(name, {}).get("downloaded") for name in record["files"])
                if complete and record.get("regenerated_from"):
                    apply_replacement(self.outputs, run_id)
                self.update(run_id, status="completed" if complete else "paused")
        except generation.BatchError as error:
            self.update(run_id, status="error", error=str(error))
        except Exception:
            # Exceptions from HTTP libraries can contain signed URLs or private data.
            self.update(run_id, status="error", error="A local file or service error interrupted this run. Saved jobs are preserved; Resume recovers them.")
        finally:
            for attribute in ("api_session", "storage_session"):
                session = getattr(client, attribute, None)
                if session:
                    session.close()
            with self.lock:
                self.active = self.current = self.phase = None


@web.middleware
async def local_only(request, handler):
    host = urlsplit("http://" + request.host)
    if host.hostname not in ("127.0.0.1", "localhost"):
        raise web.HTTPForbidden(text="Local access only.")
    origin = request.headers.get("Origin")
    if origin and origin != f"http://{request.host}":
        raise web.HTTPForbidden(text="Origin is not allowed.")
    if request.method not in ("GET", "HEAD") and request.headers.get("X-Pose-Studio") != "1":
        raise web.HTTPForbidden(text="Use the local Pose Studio interface.")
    try:
        response = await handler(request)
    except generation.BatchError as error:
        response = web.json_response({"error": str(error)}, status=400)
    except (ValueError, KeyError, TypeError):
        response = web.json_response({"error": "Invalid request."}, status=400)
    response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
    return response


async def body_json(request):
    content = await read_body(request, 64 * 1024)
    body = json.loads(content)
    if not isinstance(body, dict):
        raise generation.BatchError("Expected a JSON object.")
    return body


async def read_body(request, limit=MAX_IMAGE):
    result = bytearray()
    async for chunk in request.content.iter_chunked(64 * 1024):
        result.extend(chunk)
        if len(result) > limit:
            raise generation.BatchError("Upload exceeds the size limit.")
    return bytes(result)


async def config(request):
    studio = request.app[STUDIO]
    try:
        generation.read_key()
        key_ready = True
    except (generation.BatchError, ValueError, OSError):
        key_ready = False
    try:
        pose_metadata.api_key("xai")
        xai_ready = True
    except (generation.BatchError, ValueError, OSError):
        xai_ready = False
    profiles = []
    for name, label, filename, order in (
        ("v2", "Mannequin v2 · source first", "pose-mannequin-v2.txt", "source-first"),
        ("v3", "Mannequin v3 · remove bedding", "pose-mannequin-v3.txt", "source-first"),
        ("original", "Original · style first", "pose-mannequin.txt", "style-first"),
    ):
        path = ROOT / "scripts/prompts" / filename
        if path.is_file():
            profiles.append({"id": name, "label": label, "prompt": path.read_text().strip(), "image_order": order})
    return web.json_response({"key_ready": key_ready, "profiles": profiles, "style_uri": DEFAULT_STYLE,
                              "drawing_editor": True, "caption_queue": True,
                              "tag_groups": studio.tag_groups,
                              "default_caption_prompt": pose_metadata.DEFAULT_PROMPT, "metadata_models": pose_metadata.MODELS,
                              "metadata_providers": {"xai": xai_ready, "spicy": key_ready},
                              "regeneration_supported": True, "inline_regeneration": True, "regeneration_queue": True,
                              "staged_available": (studio.root / ".cache/pose-sources/staged_v2").is_dir()})


async def sources(request):
    studio = request.app[STUDIO]
    if request.method == "GET":
        return web.json_response(studio.public_source(studio.source(request.match_info["id"])))
    body = await body_json(request)
    record = studio.staged() if body.get("preset") == "staged" else studio.new_source(body.get("label"))
    return web.json_response(studio.public_source(record))


async def upload_source(request):
    content = await read_body(request)
    # File validation/writes stay sequential: two uploads cannot overwrite a manifest.
    request.app[STUDIO].add_image(request.match_info["id"], request.query.get("name", ""), content)
    return web.json_response({"saved": True})


async def upload_style(request):
    content = await read_body(request)
    check_image(content)
    identity = uuid.uuid4().hex
    generation.atomic_write(request.app[STUDIO].cache / "styles" / (identity + ".image"), content)
    return web.json_response({"id": identity, "url": f"/media/style/{identity}"})


async def runs(request):
    studio = request.app[STUDIO]
    if request.method == "GET":
        return web.json_response(sorted((studio.summary(p.parent.name, False)
            for p in studio.outputs.glob("*/studio.json") if not read_json(p).get("regenerated_from")),
            key=lambda r: r["created"], reverse=True))
    studio.available()
    body = await body_json(request)
    studio.available()  # Another request could have started while this body streamed.
    identity = studio.create_run(body)
    studio.start(identity)
    return web.json_response(studio.summary(identity), status=201)


async def run_detail(request):
    studio = request.app[STUDIO]
    return web.json_response(studio.summary(root_run_id(studio.outputs, request.match_info["id"])))


async def run_action(request):
    studio, identity = request.app[STUDIO], request.match_info["id"]
    studio.run(identity)
    action = request.match_info["action"]
    if action == "pause":
        studio.pause(identity)
    elif action == "resume":
        studio.start(identity)
    else:
        raise web.HTTPNotFound()
    return web.json_response(studio.summary(root_run_id(studio.outputs, identity)))


async def regenerate(request):
    studio = request.app[STUDIO]
    body = await body_json(request)
    identity = studio.regenerate(request.match_info["id"], body.get("name"), body.get("request_id"))
    studio.enqueue(identity)
    return web.json_response(studio.summary(root_run_id(studio.outputs, identity)))


async def curate(request):
    studio, identity = request.app[STUDIO], request.match_info["id"]
    body = await body_json(request)
    action = request.match_info["operation"]
    if action == "metadata":
        studio.start_metadata(identity, body.get("provider"), body.get("limit"), body.get("caption_prompt"))
    elif action == "caption":
        studio.enqueue_caption(identity, body)
    elif action == "resume-captions":
        studio.resume_captions(identity)
    elif action == "depth":
        studio.start_depth(identity, body.get("limit"))
    elif action == "edit-drawing":
        studio.edit_drawing(identity, body)
    elif action == "caption-prompt":
        studio.save_caption_prompt(identity, body.get("caption_prompt"))
    elif action == "rename":
        studio.rename_folder(identity, body.get("label"))
    elif action == "add":
        studio.add_to_folder(identity, body.get("source_id"), body.get("kind"))
    else:
        studio.set_deleted(identity, body.get("name"), deleted=action == "delete")
    return web.json_response(studio.summary(identity))


async def tag_settings(request):
    studio = request.app[STUDIO]
    body = await body_json(request)
    groups = pose_metadata.pose_tags.validate(body.get("groups"))
    if body.get("version") != pose_metadata.pose_tags.version(studio.tag_groups):
        raise generation.BatchError("The global tags changed in another tab. Reload before saving.")
    if any(status.get("running") for status in studio.metadata_status.values()) or any(not t.done() for t in studio.caption_workers.values()):
        raise generation.BatchError("Wait for the current metadata pass before changing global tags.")
    generation.write_json(studio.tag_path, groups)
    studio.tag_groups = groups
    for path in studio.outputs.glob("*/batch.json"):
        state = read_json(path)
        if state.get("metadata"):
            try:
                with generation.batch_lock(path.parent):
                    generation.write_catalog(path.parent, read_json(path))
            except generation.BatchError:
                pass  # An active generation writes its catalog when it finishes.
    studio.save_queue()
    return web.json_response({"groups": groups, "version": pose_metadata.pose_tags.version(groups)})


async def media(request):
    studio, identity = request.app[STUDIO], request.match_info["id"]
    kind = request.match_info["kind"]
    if kind == "source":
        record, name = studio.source(identity), request.query.get("name", "")
        if name not in record["files"]:
            raise web.HTTPNotFound()
        path = child(Path(record["directory"]), name)
    elif kind == "style":
        path = studio.style_path(identity)
    else:
        record = studio.run(identity)
        index = int(request.match_info["index"])
        if not 0 <= index < len(record["files"]):
            raise web.HTTPNotFound()
        batch = read_json(studio.run_path(identity) / "batch.json")
        name = record["files"][index]
        if name in batch.get("trash", {}):
            raise web.HTTPNotFound()
        job = batch.get("replacements", {}).get(name) or batch["jobs"].get(name, {})
        if not job.get("downloaded"):
            raise web.HTTPNotFound()
        relative = "depth/" + job["output_sha256"] + ".png" if request.query.get("depth") == "1" else job["output"]
        path = child(studio.run_path(identity), relative)
    if not path.is_file():
        raise web.HTTPNotFound()
    response = web.FileResponse(path)
    if kind == "style":
        mime, _ = generation.image_details(path.read_bytes())
        response.content_type = mime
    return response


async def static(request):
    filename = request.match_info.get("filename", "index.html")
    if filename not in ("index.html", "studio.css", "studio.js"):
        raise web.HTTPNotFound()
    return web.FileResponse(UI / filename)


def create_app(root=ROOT, client_factory=None):
    app = web.Application(middlewares=[local_only], client_max_size=MAX_IMAGE + 1024)
    app[STUDIO] = Studio(root, client_factory)
    app.router.add_get("/", static)
    app.router.add_get("/static/{filename}", static)
    app.router.add_get("/api/config", config)
    app.router.add_post("/api/tag-groups", tag_settings)
    app.router.add_post("/api/sources", sources)
    app.router.add_get("/api/sources/{id}", sources)
    app.router.add_put("/api/sources/{id}/files", upload_source)
    app.router.add_post("/api/style", upload_style)
    app.router.add_get("/api/runs", runs)
    app.router.add_post("/api/runs", runs)
    app.router.add_get("/api/runs/{id}", run_detail)
    app.router.add_post("/api/runs/{id}/regenerate", regenerate)
    app.router.add_post("/api/runs/{id}/{operation:delete|restore|metadata|caption|resume-captions|depth|edit-drawing|caption-prompt|rename|add}", curate)
    app.router.add_post("/api/runs/{id}/{action}", run_action)
    app.router.add_get("/media/{kind:source|style}/{id}", media)
    app.router.add_get("/media/{kind:result}/{id}/{index}", media)

    async def shutdown(application):
        studio = application[STUDIO]
        studio.closing = True
        studio.queue_paused = True
        studio.pause_requested.set()
        studio.save_queue()
        if studio.task and not studio.task.done():
            print("Finishing the current accepted image before stopping. Saved jobs can be resumed.", flush=True)
            await studio.task
        await asyncio.gather(*studio.caption_workers.values(), *studio.metadata_tasks.values(), *studio.depth_tasks.values())

    app.on_shutdown.append(shutdown)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open the browser when the server is ready")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    try:
        # Acquire this before restoring runs: a second launcher must not mark
        # another server's active work as interrupted.
        with generation.batch_lock(ROOT / ".cache/pose-studio"):
            app = create_app()
            if args.open:
                async def open_browser(application):
                    asyncio.get_running_loop().call_later(0.5, webbrowser.open, f"http://127.0.0.1:{args.port}")
                app.on_startup.append(open_browser)
            web.run_app(app, host="127.0.0.1", port=args.port, access_log=None)
    except generation.BatchError:
        raise SystemExit("Pose Studio is already running for this project. Use its existing browser tab.") from None
    except OSError as error:
        raise SystemExit(f"Could not start Pose Studio on port {args.port}: {error.strerror}") from None


if __name__ == "__main__":
    main()
