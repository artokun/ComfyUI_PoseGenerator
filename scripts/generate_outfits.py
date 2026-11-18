#!/usr/bin/env python3
"""Render outfit references with an OpenAI image model, one ghost-mannequin image per spec.

A paid development tool (like the Pose Studio helpers); the ComfyUI node never calls it.
Every outfit is rendered from its text description, using an existing outfit image only
as the presentation reference (ghost mannequin, framing, lighting, background), so the
whole set looks consistent.

    OPENAI_API_KEY=... python scripts/generate_outfits.py \\
        --specs scripts/outfits/outfit_specs.json \\
        --reference "assets/shipped/Outfits/img/preppy_O001.webp" \\
        --out .cache/outfit-renders --model gpt-image-2

Finished PNGs are skipped on a rerun, so an interrupted batch resumes.

Batch mode (half price, results within 24 h) submits every missing outfit as one OpenAI
Batch job, then a second call collects it:

    python scripts/generate_outfits.py --reference .cache/outfit-baseline.png --batch
    python scripts/generate_outfits.py --collect      # repeat until the batch is done

The batch id is kept in <out>/batch.json.
"""
import argparse
import base64
import io
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "scripts/prompts/outfit-ghost-mannequin.txt"
API = "https://api.openai.com/v1/images/edits"
API_TEXT_ONLY = "https://api.openai.com/v1/images/generations"
API_ROOT = "https://api.openai.com/v1"
REFERENCE_NOTE = (
    "Use the reference image ONLY for presentation: the same ghost-mannequin style, front view, "
    "framing, scale, soft studio lighting and plain light-grey seamless background. Do not reuse any "
    "of its garments, colours or patterns; render only the outfit described here."
)


def outfit_prompt(spec, with_reference=True):
    body = TEMPLATE.read_text(encoding="utf-8").strip()
    garments = spec["prompt"].removeprefix("wearing ").strip()
    text = body.replace("{outfit}", "The outfit: " + garments)
    return text + " " + REFERENCE_NOTE if with_reference else text


def reference_png(path):
    with Image.open(path) as image:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, "PNG")
        return buffer.getvalue()


def render(spec, reference, args, key):
    target = args.out / f"{spec['id']}.png"
    if target.is_file():
        return spec["id"], "skipped"
    data = {"model": args.model, "prompt": outfit_prompt(spec, reference is not None), "size": args.size, "n": 1}
    if args.quality:
        data["quality"] = args.quality
    last = ""
    for attempt in range(4):
        try:
            headers = {"Authorization": f"Bearer {key}"}
            if reference is None:  # text only: the set's baseline look comes from the model itself
                response = requests.post(API_TEXT_ONLY, headers=headers, json=data, timeout=300)
            else:
                response = requests.post(API, headers=headers, data={**data, "n": "1"},
                                         files={"image[]": ("reference.png", reference, "image/png")}, timeout=300)
            if response.status_code == 200:
                payload = response.json()["data"][0]
                content = base64.b64decode(payload["b64_json"])
                tmp = target.with_suffix(".part")
                tmp.write_bytes(content)
                tmp.replace(target)
                return spec["id"], "ok"
            last = f"HTTP {response.status_code}: {response.text[:300]}"
            if response.status_code in (400, 401, 403):
                break  # not worth retrying (bad request, bad key, refused)
        except requests.RequestException as error:
            last = str(error)
        time.sleep(5 * (attempt + 1))
    return spec["id"], "failed: " + last


def _check(response):
    if response.status_code >= 300:
        sys.exit(f"HTTP {response.status_code}: {response.text[:500]}")
    return response.json()


def submit_batch(specs, args, key):
    headers = {"Authorization": f"Bearer {key}"}
    todo = [spec for spec in specs if not (args.out / f"{spec['id']}.png").is_file()]
    if not todo:
        sys.exit("Nothing to render.")
    endpoint = "/v1/images/generations"
    reference_id = None
    if not args.no_reference:
        endpoint = "/v1/images/edits"
        upload = _check(requests.post(f"{API_ROOT}/files", headers=headers, data={"purpose": "vision"},
                                      files={"file": ("reference.png", reference_png(args.reference), "image/png")}, timeout=300))
        reference_id = upload["id"]
    lines = []
    for spec in todo:
        body = {"model": args.model, "prompt": outfit_prompt(spec, reference_id is not None), "size": args.size, "n": 1}
        if args.quality:
            body["quality"] = args.quality
        if reference_id:
            body["images"] = [{"file_id": reference_id}]
        lines.append(json.dumps({"custom_id": spec["id"], "method": "POST", "url": endpoint, "body": body}))
    requests_file = _check(requests.post(f"{API_ROOT}/files", headers=headers, data={"purpose": "batch"},
                                         files={"file": ("outfits.jsonl", "\n".join(lines).encode(), "application/jsonl")}, timeout=300))
    batch = _check(requests.post(f"{API_ROOT}/batches", headers=headers, timeout=60, json={
        "input_file_id": requests_file["id"], "endpoint": endpoint, "completion_window": "24h"}))
    (args.out / "batch.json").write_text(json.dumps({"batch_id": batch["id"], "outfits": [s["id"] for s in todo]}, indent=1))
    print(json.dumps({"batch_id": batch["id"], "status": batch["status"], "requests": len(todo), "endpoint": endpoint}))


def collect_batch(args, key):
    headers = {"Authorization": f"Bearer {key}"}
    state = json.loads((args.out / "batch.json").read_text())
    batch = _check(requests.get(f"{API_ROOT}/batches/{state['batch_id']}", headers=headers, timeout=60))
    print(json.dumps({"status": batch["status"], "counts": batch.get("request_counts"), "errors": batch.get("errors")}))
    if batch.get("error_file_id"):
        errors = requests.get(f"{API_ROOT}/files/{batch['error_file_id']}/content", headers=headers, timeout=300)
        for line in errors.text.splitlines():
            row = json.loads(line)
            print(row["custom_id"], "failed:", json.dumps(row.get("response", {}).get("body") or row.get("error"))[:300])
    if not batch.get("output_file_id"):
        sys.exit(0 if batch["status"] in ("validating", "in_progress", "finalizing") else 1)
    output = requests.get(f"{API_ROOT}/files/{batch['output_file_id']}/content", headers=headers, timeout=600, stream=True)
    output.raise_for_status()
    saved = 0
    for line in output.iter_lines():
        if not line:
            continue
        row = json.loads(line)
        response = row.get("response") or {}
        if response.get("status_code") != 200:
            print(row["custom_id"], "failed:", json.dumps(response.get("body") or row.get("error"))[:300])
            continue
        target = args.out / f"{row['custom_id']}.png"
        tmp = target.with_suffix(".part")
        tmp.write_bytes(base64.b64decode(response["body"]["data"][0]["b64_json"]))
        tmp.replace(target)
        saved += 1
    print(json.dumps({"saved": saved}))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--specs", type=Path, default=ROOT / "scripts/outfits/outfit_specs.json")
    parser.add_argument("--reference", type=Path, default=ROOT / "assets/shipped/Outfits/img/preppy_O001.webp")
    parser.add_argument("--out", type=Path, default=ROOT / ".cache/outfit-renders")
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--size", default="1024x1536")
    parser.add_argument("--quality", default="high")
    parser.add_argument("--only", nargs="*", help="Render only these outfit ids")
    parser.add_argument("--no-reference", action="store_true", help="Text only: no reference image (use for the baseline outfit)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch", action="store_true", help="Submit missing outfits as one OpenAI Batch job (half price)")
    parser.add_argument("--collect", action="store_true", help="Check the submitted batch and save finished images")
    args = parser.parse_args()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        sys.exit("Set OPENAI_API_KEY.")
    specs = json.loads(args.specs.read_text(encoding="utf-8"))
    if args.only:
        specs = [spec for spec in specs if spec["id"] in set(args.only)]
    args.out.mkdir(parents=True, exist_ok=True)
    if args.collect:
        return collect_batch(args, key)
    if args.batch:
        return submit_batch(specs, args, key)
    reference = None if args.no_reference else reference_png(args.reference)
    failures = 0
    with ThreadPoolExecutor(args.workers) as pool:
        jobs = [pool.submit(render, spec, reference, args, key) for spec in specs]
        for job in as_completed(jobs):
            outfit_id, status = job.result()
            failures += status.startswith("failed")
            print(f"{outfit_id}: {status}", flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
