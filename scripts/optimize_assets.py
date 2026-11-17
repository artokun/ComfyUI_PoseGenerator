#!/usr/bin/env python3
"""Explicit, resumable Tinify batch. Originals are never overwritten."""

import argparse
import hashlib
import io
import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.tinify.com"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_key():
    key = os.environ.get("TINIFY_API_KEY", "")
    if not key and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text().splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "TINIFY_API_KEY":
                key = value.strip().strip("\"'")
    if not key:
        raise ValueError("Set TINIFY_API_KEY in the environment or the project .env.")
    return key


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def image_info(content):
    with Image.open(io.BytesIO(content)) as image:
        image.load()
        return image.format, image.size


class Optimizer:
    def __init__(self, source, destination, key, jobs, rate=12):
        self.source = source
        self.destination = destination
        self.auth = ("api", key)
        self.jobs = jobs
        self.rate = rate
        self.next_request = 0.0
        self.lock = threading.Lock()
        self.local = threading.local()
        self.stop = threading.Event()
        self.receipt = destination / "tinify-receipts.jsonl"
        self.records = {}
        self.count = 0
        self.api_count = 0
        self.errors = []
        destination.mkdir(parents=True, exist_ok=True)
        if self.receipt.exists():
            for line in self.receipt.read_text().splitlines():
                if line.strip():
                    record = json.loads(line)
                    self.records[record["source_path"]] = record

    def record(self, value):
        with self.lock:
            with self.receipt.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(value) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self.records[value["source_path"]] = value

    def request(self, method, url, **kwargs):
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "api.tinify.com":
            raise ValueError("Refusing to send Tinify credentials to an unexpected host.")
        for attempt in range(8):
            if self.stop.is_set():
                raise RuntimeError("Batch stopped after an account or API error.")
            while True:
                with self.lock:
                    delay = self.next_request - time.monotonic()
                    if delay <= 0:
                        self.next_request = time.monotonic() + 1 / self.rate
                        break
                time.sleep(min(delay, 1))
            try:
                if not hasattr(self.local, "session"):
                    self.local.session = requests.Session()
                    self.local.session.auth = self.auth
                response = self.local.session.request(method, url, timeout=(20, 180), allow_redirects=False, **kwargs)
            except (requests.Timeout, requests.ConnectionError):
                if attempt == 7:
                    raise RuntimeError("Tinify connection failed; the batch can be resumed.") from None
                time.sleep(2 ** attempt)
                continue
            with self.lock:
                self.api_count = max(self.api_count, int(response.headers.get("Compression-Count", 0)))
            if response.status_code < 300:
                return response
            try:
                details = response.json()
            except ValueError:
                details = {}
            message = str(details.get("message", "API request failed"))
            lower_message = message.lower()
            monthly_limit = any(word in lower_message for word in ("month", "quota")) or ("limit" in lower_message and "rate" not in lower_message)
            if response.status_code in (401, 402) or (response.status_code == 429 and monthly_limit):
                self.stop.set()
                raise RuntimeError(f"Tinify account error ({response.status_code}): {message}")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 7:
                    with self.lock:
                        if response.status_code == 429:
                            self.rate = max(1, self.rate * 0.75)
                        self.next_request = max(self.next_request, time.monotonic() + min(30, 2 ** (attempt + 1)))
                    continue
            raise RuntimeError(f"Tinify error ({response.status_code}): {message}")
        raise RuntimeError("Tinify retry limit reached.")

    def optimize(self, relative):
        if self.stop.is_set():
            return
        source_path = self.source / relative
        content = source_path.read_bytes()
        source_hash = sha256(content)
        output_relative = str(Path(relative).with_suffix(".webp"))
        output = self.destination / output_relative
        prior = self.records.get(relative, {})
        if prior.get("source_sha256") == source_hash and prior.get("status") == "done" and output.is_file():
            if sha256(output.read_bytes()) == prior.get("output_sha256"):
                with self.lock:
                    self.count += 1
                return
        source_format, dimensions = image_info(content)
        base = {"source_path": relative, "output_path": output_relative, "source_sha256": source_hash,
                "input_bytes": len(content), "width": dimensions[0], "height": dimensions[1]}
        url = None
        if prior.get("source_sha256") == source_hash and time.time() - prior.get("uploaded_at", 0) < 7 * 3600:
            url = prior.get("url")
        if not url:
            response = self.request("POST", API + "/shrink", data=content, headers={"Content-Type": "application/octet-stream"})
            url = response.headers.get("Location") or response.json()["output"]["url"]
            base.update({"url": url, "uploaded_at": time.time()})
            self.record({**base, "status": "uploaded"})
        else:
            base.update({"url": url, "uploaded_at": prior["uploaded_at"]})
        if source_format == "WEBP":
            response = self.request("GET", url)
        else:
            response = self.request("POST", url, json={"convert": {"type": "image/webp"}})
        optimized = response.content
        output_format, output_dimensions = image_info(optimized)
        if output_format != "WEBP" or output_dimensions != dimensions:
            raise ValueError(f"Tinify returned an unexpected format or dimensions for {relative}.")
        kept_original = source_format == "WEBP" and len(content) <= len(optimized)
        if kept_original:
            optimized = content
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(".webp.tmp")
        temporary.write_bytes(optimized)
        temporary.replace(output)
        self.record({**base, "status": "done", "output_bytes": len(optimized), "output_sha256": sha256(optimized),
                     "kept_smaller_original_webp": kept_original, "completed_at": time.time()})
        with self.lock:
            self.count += 1
            if self.count % 50 == 0:
                print(f"Verified {self.count} WebP files; monthly compression count {self.api_count}", flush=True)

    def run(self, limit=0):
        manifest = json.loads((self.source / "source.json").read_text())
        files = [entry["path"] for entry in manifest["files"] if entry["path"].startswith(("img/", "thumbs/"))]
        outputs = [str(Path(relative).with_suffix(".webp")) for relative in files]
        if len(outputs) != len(set(outputs)):
            raise ValueError("WebP conversion would create a filename collision.")
        batch = files[:limit] if limit else files
        print(f"Optimizing {len(batch)} files with {self.jobs} workers; resumable receipts enabled.", flush=True)
        with ThreadPoolExecutor(max_workers=self.jobs) as executor:
            futures = {executor.submit(self.optimize, relative): relative for relative in batch}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as error:
                    self.errors.append({"path": futures[future], "error": str(error)})
                    if len(self.errors) <= 5:
                        print(f"Error: {futures[future]}: {error}", flush=True)
        done = [self.records[path] for path in files if self.records.get(path, {}).get("status") == "done"]
        summary = {"source_revision": manifest["revision"], "expected_files": len(files), "completed_files": len(done),
                   "input_bytes": sum(row["input_bytes"] for row in done),
                   "output_bytes": sum(row["output_bytes"] for row in done),
                   "monthly_compression_count": self.api_count, "errors": self.errors,
                   "complete": len(done) == len(files) and not self.errors}
        write_json(self.destination / "optimization.json", summary)
        if summary["complete"]:
            catalog = json.loads((self.source / "catalog.json").read_text())
            for kind in ("pose", "outfit"):
                for entry in catalog[kind]:
                    for field in ("image", "thumbnail"):
                        entry[field] = str(Path(entry[field]).with_suffix(".webp"))
            for name in ("LICENSE", "source.json"):
                shutil.copy2(self.source / name, self.destination / name)
            shutil.copytree(self.source / "data", self.destination / "data", dirs_exist_ok=True)
            write_json(self.destination / "catalog.json", catalog)
        print(json.dumps({key: value for key, value in summary.items() if key != "errors"}), flush=True)
        if self.errors:
            raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description="Optimize both gallery folders as WebP using Tinify; retain originals.")
    parser.add_argument("--source", type=Path, default=ROOT / "assets/gallery")
    parser.add_argument("--destination", type=Path, default=ROOT / "assets/gallery-webp")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--rate", type=float, default=12, help="Maximum requests per second, reduced automatically on rate limiting")
    parser.add_argument("--limit", type=int, default=0, help="Process a small first batch before resuming the full run")
    args = parser.parse_args()
    if args.jobs < 1 or args.jobs > 20:
        parser.error("--jobs must be between 1 and 20")
    if not 0 < args.rate <= 20:
        parser.error("--rate must be greater than zero and at most 20")
    if args.source.resolve() == args.destination.resolve():
        parser.error("Source and destination must differ to preserve the originals")
    Optimizer(args.source.resolve(), args.destination.resolve(), read_key(), args.jobs, args.rate).run(args.limit)


if __name__ == "__main__":
    main()
