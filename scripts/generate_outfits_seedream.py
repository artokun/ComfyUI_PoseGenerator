#!/usr/bin/env python3
"""Render outfit specs with Seedream 5.5 (SpicyAPI), for outfits the OpenAI model refused.

Same ghost-mannequin prompt and presentation reference as generate_outfits.py. Jobs run one
at a time, each quoted first and held to --max-per-image and --max-total (USD).

    SPICY_API_KEY=... python scripts/generate_outfits_seedream.py \\
        --specs scripts/outfits/outfit_specs_extra.json --only lingerie_O001 lingerie_O002 \\
        --reference .cache/outfit-baseline.png --out .cache/outfit-renders-extra

Finished PNGs are skipped on a rerun. Job ids are kept in <out>/seedream.json.
"""
import argparse
import json
import sys
import uuid
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_outfits import outfit_prompt, reference_png  # noqa: E402
from generate_pose_references import (  # noqa: E402
    MODEL, BatchError, SpicyClient, atomic_write, image_details, money, read_key, write_json,
)

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--specs", type=Path, default=ROOT / "scripts/outfits/outfit_specs.json")
    parser.add_argument("--reference", type=Path, default=ROOT / ".cache/outfit-baseline.png")
    parser.add_argument("--out", type=Path, default=ROOT / ".cache/outfit-renders")
    parser.add_argument("--only", nargs="*", help="Render only these outfit ids")
    parser.add_argument("--resolution", choices=("1k", "1.5k", "2k"), default="2k")
    parser.add_argument("--max-per-image", default="0.051")
    parser.add_argument("--max-total", default="1.00")
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()

    specs = json.loads(args.specs.read_text(encoding="utf-8"))
    if args.only:
        specs = [spec for spec in specs if spec["id"] in set(args.only)]
    args.out.mkdir(parents=True, exist_ok=True)
    state_path = args.out / "seedream.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {"jobs": {}}
    client = SpicyClient(read_key())
    reference_uri = None
    failures = 0
    for spec in specs:
        target = args.out / f"{spec['id']}.png"
        if target.is_file():
            print(f"{spec['id']}: skipped", flush=True)
            continue
        try:
            job = state["jobs"].get(spec["id"])
            if not job:
                if reference_uri is None:
                    reference_uri = client.upload(reference_png(args.reference))["uri"]
                request = {"model": MODEL, "input": {
                    "image_urls": [reference_uri], "prompt": outfit_prompt(spec, True),
                    "aspect_ratio": "2:3", "resolution": args.resolution,
                    "output_format": "png", "background": "opaque",
                }}
                quoted = client.api("POST", "/jobs/quote", payload=request)
                charge = money(quoted["maxCharge"])
                spent = sum((money(j["max_charge"]) for j in state["jobs"].values()), Decimal(0))
                if quoted.get("currency") != "USD" or charge > money(args.max_per_image) or spent + charge > money(args.max_total):
                    raise BatchError(f"quote {charge} exceeds the spending cap; no task created")
                request.update(quoteId=quoted["quoteId"], expectedCost=quoted["estimatedCost"])
                job = {"max_charge": str(charge), "idempotency_key": str(uuid.uuid4()), "payload": request}
                state["jobs"][spec["id"]] = job
                write_json(state_path, state)
            if not job.get("task_id"):
                created = client.api("POST", "/jobs/createTask", payload=job["payload"], idempotency_key=job["idempotency_key"])
                job["task_id"] = created["taskId"]
                write_json(state_path, state)
            record = client.poll(job["task_id"], args.timeout, lambda r: None)
            content = client.storage("GET", record["output"]["assets"][0]["url"]).content
            image_details(content)
            atomic_write(target, content)
            job["state"] = record.get("state")
            job["cost"] = record.get("cost")
            write_json(state_path, state)
            print(f"{spec['id']}: ok", flush=True)
        except BatchError as error:
            failures += 1
            print(f"{spec['id']}: failed: {error}", flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
