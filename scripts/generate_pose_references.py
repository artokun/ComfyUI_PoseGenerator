#!/usr/bin/env python3
"""Resumable, sequential SpicyAPI mannequin edits with a persistent spend cap."""

import argparse
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shlex
import tempfile
import time
from urllib.parse import quote, urlparse
import uuid

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://api.spicyapi.ai/api/v1"
MODEL = "bytedance/seedream-5.5/edit"
RATIOS = ("1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9")
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
TERMINAL = {"succeeded", "failed", "canceled", "expired"}
REPLAY_WINDOW = 23 * 3600  # Stop before the provider's 24-hour idempotency expiry.


class BatchError(RuntimeError):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def money(value):
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise BatchError("Invalid USD amount.") from None
    if not amount.is_finite() or amount < 0:
        raise BatchError("USD amounts must be finite and nonnegative.")
    return amount


def read_key(names=("SPICY_API_KEY", "SAI_API_TOKEN")):
    for name in names:
        if os.environ.get(name):
            return os.environ[name]
    values = {}
    if (ROOT / ".env").is_file():
        for line in (ROOT / ".env").read_text().splitlines():
            name, separator, value = line.removeprefix("export ").partition("=")
            if separator and name.strip() in names:
                parsed = shlex.split(value, comments=True)
                if len(parsed) == 1:
                    values[name.strip()] = parsed[0]
    for name in names:
        if values.get(name):
            return values[name]
    raise BatchError(f"Set {' or '.join(names)} in the environment or project .env.")


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    atomic_write(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode())


@contextmanager
def batch_lock(destination):
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / ".batch.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BatchError("Another process is using this destination.") from None
        yield


def image_details(content):
    with Image.open(io.BytesIO(content)) as image:
        fmt, size = image.format, image.size
        if image.getexif().get(274) in (5, 6, 7, 8):
            size = size[::-1]
        image.load()
    mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(fmt)
    if not mime:
        raise BatchError("Only PNG, JPEG, and WebP input images are supported.")
    return mime, size


def closest_ratio(size):
    ratio = size[0] / size[1]
    return min(RATIOS, key=lambda text: abs(math.log(ratio / (int(text.split(':')[0]) / int(text.split(':')[1])))))


def waiting_for_assets(record):
    return record.get("state") == "succeeded" and any(
        asset.get("pending") and not asset.get("unavailable")
        for asset in (record.get("output") or {}).get("assets", [])
    )


def configuration_mismatch(saved, current):
    differences = []
    if saved.get("source") != current.get("source"):
        differences.append(f"source directory: saved {saved.get('source')!r}; current {current.get('source')!r}")
    if saved.get("key_fingerprint") != current.get("key_fingerprint"):
        differences.append("API key differs (exported SPICY_API_KEY or SAI_API_TOKEN overrides project .env)")
    old_recipe, new_recipe = saved.get("recipe", {}), current.get("recipe", {})
    for field in sorted(old_recipe.keys() | new_recipe.keys()):
        if old_recipe.get(field) != new_recipe.get(field):
            if field == "prompt":
                differences.append("prompt text differs from the saved batch")
            else:
                differences.append(f"{field}: saved {old_recipe.get(field)!r}; current {new_recipe.get(field)!r}")
    details = "\n".join(f"  - {difference}" for difference in differences) or "  - batch metadata differs"
    return (
        "Batch configuration mismatch:\n" + details +
        "\nResume with the original settings and API key, or use a new --destination for a fresh batch."
    )


class SpicyClient:
    def __init__(self, key):
        self.key_fingerprint = sha256(key.encode())
        self.api_session = requests.Session()
        self.api_session.trust_env = False
        self.api_session.headers["Authorization"] = f"Bearer {key}"
        # Separate session: neither bearer auth nor .netrc credentials reach storage.
        self.storage_session = requests.Session()
        self.storage_session.trust_env = False

    def api(self, method, path, *, payload=None, params=None, idempotency_key=None):
        if not path.startswith("/") or path.startswith("//") or ":" in path:
            raise BatchError("Invalid API path.")
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        for attempt in range(4):
            try:
                response = self.api_session.request(
                    method, BASE + path, json=payload, params=params, headers=headers,
                    timeout=(15, 30), allow_redirects=False,
                )
            except requests.RequestException:
                if attempt == 3:
                    raise BatchError("Cannot reach SpicyAPI. Saved jobs can be resumed with the same command.") from None
                time.sleep(2 ** attempt)
                continue
            try:
                body = response.json()
            except ValueError:
                body = {}
            code = body.get("code")
            if response.status_code in (200, 202) and code == 200 and isinstance(body.get("data"), dict):
                return body["data"]
            retryable = response.status_code == 429 or response.status_code >= 500 or code in (429, 500, 50301)
            if retryable and attempt < 3:
                retry_after = response.headers.get("Retry-After", "")
                time.sleep(min(30, int(retry_after)) if retry_after.isdigit() else 2 ** (attempt + 1))
                continue
            # Do not print response bodies, which may echo private request data.
            raise BatchError(f"SpicyAPI error: HTTP {response.status_code}, code {code}. No new job will be substituted.")
        raise BatchError("SpicyAPI retry limit reached.")

    def storage(self, method, url, **kwargs):
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise BatchError("Storage URL must be HTTPS without embedded credentials.")
        if any(key.lower() == "authorization" for key in kwargs.get("headers", {})):
            raise BatchError("Refusing to send Authorization to storage.")
        try:
            response = self.storage_session.request(method, url, timeout=(15, 120), allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise BatchError("Storage transfer failed. Resume to recover the existing job.") from None
        if not 200 <= response.status_code < 300:
            raise BatchError(f"Storage transfer returned HTTP {response.status_code}. Resume to refresh result links.")
        return response

    def upload(self, content):
        mime, _ = image_details(content)
        if len(content) > 10 * 1024 * 1024:
            raise BatchError("Reference image exceeds the 10 MiB upload limit.")
        ticket = self.api("POST", "/common/upload-url", payload={"contentType": mime, "bytes": len(content)})
        if ticket.get("method") != "PUT" or len(content) > ticket["maxBytes"]:
            raise BatchError("Unexpected upload ticket.")
        self.storage("PUT", ticket["uploadUrl"], headers=ticket["headers"], data=content)
        committed = self.api("POST", f"/files/{quote(ticket['fileId'], safe='')}/commit")
        if committed.get("status") != "ready" or not committed.get("uri", "").startswith("spicy://"):
            raise BatchError("Reference upload is not ready.")
        if committed.get("sha256") and committed["sha256"] != sha256(content):
            raise BatchError("Uploaded reference hash differs from the local file.")
        return {"uri": committed["uri"], "expiresAt": committed["expiresAt"]}

    def poll(self, task_id, timeout, observe):
        deadline = time.monotonic() + timeout
        delay = 2
        while time.monotonic() < deadline:
            record = self.api("GET", "/jobs/recordInfo", params={"taskId": task_id})
            observe(record)
            state = record.get("state")
            if state in TERMINAL and not waiting_for_assets(record):
                if state != "succeeded":
                    raise BatchError(f"Task {task_id} ended as {state}; it will not be regenerated automatically.")
                assets = (record.get("output") or {}).get("assets", [])
                if not assets or any(asset.get("unavailable") or not asset.get("url") for asset in assets):
                    raise BatchError(f"Task {task_id} has no complete downloadable output.")
                return record
            if state not in TERMINAL | {"queued", "running"}:
                raise BatchError(f"Task {task_id} returned an unknown state.")
            time.sleep(max(0, min(delay, deadline - time.monotonic())))
            delay = min(delay * 1.5, 15)
        raise BatchError(f"Task {task_id} or its assets are still pending. Resume the same command; no replacement job is needed.")


class Batch:
    def __init__(self, source, destination, recipe, client, per_image, total, timeout):
        self.source, self.destination, self.client = source, destination, client
        self.path = destination / "batch.json"
        self.per_image, self.total, self.timeout = money(per_image), money(total), timeout
        configuration = {"source": str(source), "recipe": recipe, "key_fingerprint": client.key_fingerprint}
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
            if self.state["configuration"] != configuration:
                raise BatchError(configuration_mismatch(self.state["configuration"], configuration))
        else:
            if (destination / "catalog.json").exists():
                raise BatchError("Destination contains an existing library. Use a new destination.")
            self.state = {"version": 1, "configuration": configuration, "uploads": {}, "jobs": {}}

    def save(self):
        write_json(self.path, self.state)

    def reference(self, content):
        digest = sha256(content)
        cached = self.state["uploads"].get(digest)
        if cached and datetime.fromisoformat(cached["expiresAt"].replace("Z", "+00:00")).timestamp() > time.time() + 300:
            return cached["uri"]
        uploaded = self.client.upload(content)
        self.state["uploads"][digest] = uploaded
        self.save()
        return uploaded["uri"]

    def process(self, path, style_content=None, *, relative=None):
        relative = relative or path.relative_to(self.source).as_posix()
        if relative in self.state.get("trash", {}):
            return
        content = path.read_bytes()
        digest = sha256(content)
        _, dimensions = image_details(content)
        identity = "pose-" + sha256(relative.encode())[:16]
        output_relative = f"img/{identity}.png"
        job = self.state.get("replacements", {}).get(relative) or self.state["jobs"].get(relative)
        output = self.destination / (job["output"] if job else output_relative)
        if job and job["source_sha256"] != digest:
            raise BatchError(f"Source changed since submission: {relative}. Use a new destination.")
        if job and job.get("output_sha256") and output.is_file() and sha256(output.read_bytes()) == job["output_sha256"]:
            print(f"Already saved: {relative}", flush=True)
            return
        if not job:
            recipe = self.state["configuration"]["recipe"]
            style_uri = self.reference(style_content) if style_content is not None else recipe["style"]
            source_uri = self.reference(content)
            image_urls = [style_uri, source_uri]
            if recipe.get("image_order", "style-first") == "source-first":
                image_urls = [source_uri, style_uri]
            request = {"model": MODEL, "input": {
                "image_urls": image_urls, "prompt": recipe["prompt"],
                "aspect_ratio": closest_ratio(dimensions), "resolution": recipe["resolution"],
                "output_format": "png", "background": "opaque",
            }}
            quoted = self.client.api("POST", "/jobs/quote", payload=request)
            charge = money(quoted["maxCharge"])
            reserved = sum((money(row["max_charge"]) for row in self.state["jobs"].values()), Decimal(0))
            if quoted.get("currency") != "USD" or charge > self.per_image or reserved + charge > self.total:
                raise BatchError(f"Quote exceeds the configured spending cap ({relative}). No task created.")
            request.update(quoteId=quoted["quoteId"], expectedCost=quoted["estimatedCost"])
            job = {"id": identity, "source_sha256": digest, "source_dimensions": dimensions,
                   "output": output_relative, "payload": request, "max_charge": str(charge),
                   "idempotency_key": str(uuid.uuid4()), "first_submission": time.time()}
            self.state["jobs"][relative] = job
            self.save()  # Persist the exact payload and key BEFORE any billable call.
        if not job.get("task_id"):
            if time.time() - job["first_submission"] >= REPLAY_WINDOW:
                raise BatchError("Uncertain submission is over 23 hours old. Recover its task ID from SpicyAPI logs; do not resubmit.")
            reserved = sum((money(row["max_charge"]) for row in self.state["jobs"].values()), Decimal(0))
            if money(job["max_charge"]) > self.per_image or reserved > self.total:
                raise BatchError("Saved submission exceeds the current spending cap; no create request sent.")
            created = self.client.api("POST", "/jobs/createTask", payload=job["payload"], idempotency_key=job["idempotency_key"])
            job["task_id"] = created["taskId"]
            self.save()
        print(f"Waiting: {relative} ({job['task_id']})", flush=True)

        def observe(record):
            # Keep billing and lifecycle evidence, never temporary signed download URLs.
            for field in ("state", "cost", "settled", "errorCode", "contentState"):
                if field in record:
                    job[field] = record[field]
            self.save()

        record = self.client.poll(job["task_id"], self.timeout, observe)
        assets = record["output"]["assets"]
        if len(assets) != 1:
            raise BatchError("Expected one output image; inspect the task before selecting an asset.")
        result = self.client.storage("GET", assets[0]["url"]).content
        result_mime, result_dimensions = image_details(result)
        if result_mime != "image/png":
            raise BatchError("Expected a PNG result; existing job is preserved.")
        atomic_write(output, result)
        job.update(output_sha256=sha256(result), output_dimensions=result_dimensions, downloaded=True)
        self.save()
        self.catalog()
        print(f"Saved: {output_relative}", flush=True)

    def catalog(self):
        write_catalog(self.destination, self.state)


def write_catalog(destination, state, *, changed_source=None):
    previous = {}
    catalog_path = destination / "catalog.json"
    if catalog_path.exists():
        previous = {row["id"]: row for row in json.loads(catalog_path.read_text()).get("pose", [])}
    by_image = {row["image"]: row for row in previous.values()}
    tag_path = destination.parent / "tag-groups.json"
    groups = json.loads(tag_path.read_text()) if state.get("metadata") and tag_path.is_file() else None
    poses = []
    jobs = {**state["jobs"], **state.get("replacements", {})}
    for source, job in sorted(jobs.items()):
        if source in state.get("trash", {}):
            continue
        output = destination / job["output"]
        if changed_source is not None and source != changed_source and job["output"] in by_image and output.is_file():
            poses.append(by_image[job["output"]])
            continue
        if not job.get("downloaded") or not output.is_file() or sha256(output.read_bytes()) != job["output_sha256"]:
            continue
        entry = {"id": job["id"], "label": f"Unreviewed {Path(source).stem}",
                 "prompt": "", "category": "Unreviewed", "angle": "",
                 "image": job["output"], "thumbnail": job["output"]}
        for field in ("label", "prompt", "category", "angle"):
            if field in previous.get(job["id"], {}):
                entry[field] = previous[job["id"]][field]
        metadata = current_metadata(state, source, job)
        if metadata:
            if metadata.get("review"):
                entry.update(label="Needs pose review", category="Unreviewed", prompt="", tags=["needs-review"])
            entry.update({key: metadata[key] for key in ("id", "prompt", "category", "angle", "tags") if key in metadata})
            if "label" in metadata:
                entry["label"] = metadata["label"] or Path(source).stem
            if "tag_groups" in metadata:
                entry.update(visible_tags(metadata, groups))
                if metadata.get("review") and "needs-review" not in entry["tags"]:
                    entry["tags"].append("needs-review")
        elif source in state.get("metadata", {}):
            entry.update(label=f"Unreviewed {Path(source).stem}", prompt="", category="Unreviewed", angle="")
        poses.append(entry)
    # Captions contain identity/outfit details; never use them as pose prompts.
    write_json(catalog_path, {"schema_version": 1, "pose": poses, "outfit": []})


def current_metadata(state, name, job):
    record = state.get("metadata", {}).get(name)
    if not record or record["sha256"] != job.get("output_sha256"):
        return None
    edits = record.get("edits")
    if not edits:
        return record
    result = dict(record)
    if "label" in edits:
        label = edits["label"]
        phrase = (label[:1].lower() + label[1:]).rstrip(".")
        result.update(label=label, prompt=", ".join(filter(None, (phrase, record.get("angle")))) if label else "")
    if "tag_groups" in edits:
        result["tag_groups"] = {**record.get("tag_groups", {}), **edits["tag_groups"]}
        result.update(visible_tags(result, None))
    return result


def visible_tags(metadata, groups):
    selected = metadata["tag_groups"]
    if groups is not None:
        selected = {name: [tag for tag in values if tag in selected.get(name, [])] for name, values in groups.items()}
    return {"tag_groups": selected, "tags": list(dict.fromkeys(tag for row in selected.values() for tag in row))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--style", required=True, help="Local style image, or an existing spicy://f/ or spicy://a/ reference")
    parser.add_argument("--destination", type=Path, default=ROOT / "assets/gallery-mannequin")
    parser.add_argument("--prompt", type=Path, default=ROOT / "scripts/prompts/pose-mannequin.txt")
    parser.add_argument("--image-order", choices=("style-first", "source-first"), default="style-first",
                        help="Reference order; image numbers in --prompt must match (default: style-first)")
    parser.add_argument("--resolution", choices=("1k", "1.5k", "2k"), default="2k")
    parser.add_argument("--pattern", default="*", help="Filename glob; use one filename for a representative pilot")
    parser.add_argument("--limit", type=int, default=1, help="Maximum selected images; 0 means all (default: 1)")
    parser.add_argument("--max-per-image", default="0.051", help="Maximum USD quote per image")
    parser.add_argument("--max-total", default="1.50", help="Cumulative maximum USD exposure for this destination, including earlier runs")
    parser.add_argument("--timeout", type=float, default=600, help="Polling deadline per task in seconds")
    parser.add_argument("--dry-run", action="store_true", help="Inventory only: no API requests, uploads, or file writes")
    args = parser.parse_args()
    source, destination = args.source.expanduser().resolve(), args.destination.expanduser().resolve()
    if not source.is_dir() or destination.is_relative_to(source):
        raise BatchError("Source must be a directory and destination must be outside it.")
    if args.limit < 0 or not math.isfinite(args.timeout) or args.timeout <= 0:
        raise BatchError("Limit must be nonnegative and timeout must be positive.")
    prompt = args.prompt.read_text().strip()
    if not 1 <= len(prompt) <= 5000:
        raise BatchError("Prompt must contain 1–5000 characters.")
    per_image, total = money(args.max_per_image), money(args.max_total)
    style_content = None
    if re.fullmatch(r"spicy://(?:f/fil_|a/ast_)[A-Za-z0-9_-]+", args.style):
        style = args.style
    else:
        style_content = Path(args.style).expanduser().read_bytes()
        image_details(style_content)
        style = "sha256:" + sha256(style_content)
    paths = sorted(p for p in source.rglob(args.pattern) if p.is_file() and not p.is_symlink() and p.suffix.lower() in EXTENSIONS)
    if args.limit:
        paths = paths[:args.limit]
    if not paths:
        raise BatchError("No matching source images.")
    print(f"Selected {len(paths)} image(s); per-image cap ${per_image}; cumulative cap ${total}.", flush=True)
    print("Image order: " + ("1=source, 2=style" if args.image_order == "source-first" else "1=style, 2=source"), flush=True)
    if args.dry_run:
        for path in paths:
            content = path.read_bytes()
            _, size = image_details(content)
            print(f"{path.relative_to(source)}: {size[0]}x{size[1]} -> {closest_ratio(size)}, {args.resolution}")
        print(f"Fresh-run ceiling before total cap: ${per_image * len(paths)}. No API calls made.")
        return
    recipe = {"model": MODEL, "style": style, "prompt": prompt, "resolution": args.resolution,
              "output_format": "png", "background": "opaque", "aspect_ratio": "nearest-source"}
    # Keep the original recipe shape so old style-first batches still resume.
    if args.image_order != "style-first":
        recipe["image_order"] = args.image_order
    with batch_lock(destination):
        worker = Batch(source, destination, recipe, SpicyClient(read_key()), per_image, total, args.timeout)
        for path in paths:
            worker.process(path, style_content)
        worker.catalog()


if __name__ == "__main__":
    try:
        main()
    except (BatchError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from None
    except KeyboardInterrupt:
        raise SystemExit("Stopped locally. Accepted jobs continue remotely; resume the same command to collect them.") from None
