"""Outfit page for Pose Studio: queued, resumable outfit runs.

A run is a folder of photos or a list of descriptions, worked through one item at a time:

* Photo run, up to two paid steps per photo. "Pose" redraws the person standing in an
  A-pose or I-pose on plain white. "Extract" removes the person from that result (or from
  the photo itself when posing is skipped) and keeps the clothes as a ghost mannequin.
* Text run, one paid step per description: the text becomes a ghost-mannequin outfit.

A flat lay or collage of separate items (no person) is assembled into a worn outfit in one
step, with brand names and logos removed.

Runs queue behind each other, can be paused after the current item and resumed, and never
restart paid work on their own after the server stops. Single items can be redone.

Each step runs on OpenAI (gpt-image-2) or SpicyAPI (Seedream 5.5 edit). Every result is
fitted to OUTPUT_SIZE (3:4) so the steps and the gallery line up. Finished outfits can be
saved into a folder gallery (assets/<name>/catalog.json), which the ComfyUI node lists.
"""

import asyncio
import base64
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import shutil
import threading
import uuid

from aiohttp import web
from PIL import Image
import requests

try:
    from . import generate_pose_references as generation, pose_metadata
except ImportError:
    import generate_pose_references as generation
    import pose_metadata

ROOT = generation.ROOT
PROMPTS = ROOT / "scripts/prompts"
OUTPUT_SIZE = (1152, 1536)
MAX_UPLOAD = 20 * 1024 * 1024
OPENAI = "https://api.openai.com/v1"
OPENAI_MODEL = "gpt-image-2"
POSES = {
    "a-pose": "a relaxed A-pose: arms straight and angled about 30 degrees away from the body, palms facing the thighs, feet hip-width apart",
    "i-pose": "an I-pose: arms hanging straight down close to the sides, palms facing the thighs, feet together",
}
STYLE_NOTE = (
    "Image {n} is a presentation reference ONLY: match its ghost-mannequin style, front view, framing, "
    "scale, soft studio lighting and plain light-grey background. Never add its cardigan, top, skirt, "
    "socks or loafers, or any of its colours or patterns, to this outfit."
)
GALLERY_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,59}")
OUTFIT_STUDIO = web.AppKey("outfit_studio", object)
MAX_ITEMS = 200
# Photo runs that go straight from the picture to the outfit: "skip" extracts the outfit a person
# is wearing, "collage" assembles a flat lay or collage of separate items (a Pinterest board).
SINGLE_STEP = ("skip", "collage")
ACTIVE = ("posing", "extracting")
OUTFIT_GROUPS = ("Head", "Outerwear", "Top", "Bottom", "Full body", "Underwear", "Legs", "Feet",
                 "Accessories", "Colors", "Patterns & fabrics", "Season")
CAPTION_PROMPT = """This is a ghost-mannequin product image of one women's outfit. Describe only the clothes.
Return one JSON object with exactly these keys:
- "label": a short catalog title listing the main pieces, at most 12 words, no brand names.
- "prompt": one sentence that starts with "wearing" and names every garment with its colour and fabric, then the shoes.
- "category": one style word such as Casual, Preppy, Streetwear, Formal, Business, Party, Sporty, Loungewear, Boho, Y2K, Gothic, Cottagecore, Lingerie, Swimwear.
- "tag_groups": an object whose keys are a subset of: """ + ", ".join(OUTFIT_GROUPS) + """. Each value is a list of short lowercase tags.
  Socks, tights and stockings go under Legs. Feet is footwear only. Leave out groups the outfit does not use."""


class RunStop(generation.BatchError):
    """Stops the whole run (missing key, spending cap), not just one item."""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def openai_key(root):
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return os.environ["OPENAI_API_KEY"].strip()
    env = root / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip().removeprefix("export ").strip() == "OPENAI_API_KEY":
                value = value.strip().strip("'\"")
                if value:
                    return value
    raise generation.BatchError("Set OPENAI_API_KEY in the environment or the project .env.")


def to_png(content, max_edge=2048):
    """Decode any supported upload, drop alpha onto white, cap the size, return PNG bytes."""
    with Image.open(io.BytesIO(content)) as image:
        image.load()
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            flat = Image.new("RGB", image.size, "white")
            flat.paste(image, mask=image.getchannel("A"))
            image = flat
        image = image.convert("RGB")
        image.thumbnail((max_edge, max_edge))
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        return buffer.getvalue()


def corner_colour(image):
    width, height = image.size
    points = [(2, 2), (width - 3, 2), (2, height - 3), (width - 3, height - 3)]
    pixels = sorted(image.getpixel(point) for point in points)
    return pixels[len(pixels) // 2]


def fit_output(content):
    """Scale into OUTPUT_SIZE without cropping. Any padding stretches the image's own edge
    pixels outward, so a studio backdrop's gradient continues instead of showing a seam."""
    with Image.open(io.BytesIO(content)) as image:
        image = image.convert("RGB")
        scale = min(OUTPUT_SIZE[0] / image.width, OUTPUT_SIZE[1] / image.height)
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        resized = image.resize(size, Image.LANCZOS)
        canvas = Image.new("RGB", OUTPUT_SIZE, corner_colour(image))
        left, top = (OUTPUT_SIZE[0] - size[0]) // 2, (OUTPUT_SIZE[1] - size[1]) // 2
        right, bottom = OUTPUT_SIZE[0] - size[0] - left, OUTPUT_SIZE[1] - size[1] - top
        if left:
            canvas.paste(resized.crop((0, 0, 2, size[1])).resize((left, size[1])), (0, top))
        if right:
            canvas.paste(resized.crop((size[0] - 2, 0, size[0], size[1])).resize((right, size[1])), (left + size[0], top))
        if top:
            canvas.paste(resized.crop((0, 0, size[0], 2)).resize((size[0], top)), (left, 0))
        if bottom:
            canvas.paste(resized.crop((0, size[1] - 2, size[0], size[1])).resize((size[0], bottom)), (left, top + size[1]))
        canvas.paste(resized, (left, top))
        buffer = io.BytesIO()
        canvas.save(buffer, "PNG")
        return buffer.getvalue()


def openai_render(prompt, images, key, quality="high"):
    headers = {"Authorization": f"Bearer {key}"}
    data = {"model": OPENAI_MODEL, "prompt": prompt, "size": "1024x1536", "quality": quality, "n": 1}
    try:
        if images:
            files = [("image[]", (f"image{i + 1}.png", png, "image/png")) for i, png in enumerate(images)]
            response = requests.post(f"{OPENAI}/images/edits", headers=headers, timeout=400,
                                     data={**data, "n": "1"}, files=files)
        else:
            response = requests.post(f"{OPENAI}/images/generations", headers=headers, json=data, timeout=400)
    except requests.RequestException as error:
        raise generation.BatchError(f"Could not reach OpenAI: {type(error).__name__}.") from None
    if response.status_code != 200:
        try:
            detail = response.json().get("error", {})
        except ValueError:
            detail = {}
        if detail.get("code") == "moderation_blocked" or "safety" in str(detail.get("message", "")).lower():
            raise generation.BatchError("OpenAI refused this image (moderation). Try the SpicyAI provider.")
        message = str(detail.get("message") or response.reason)[:300]
        raise generation.BatchError(f"OpenAI error {response.status_code}: {message}")
    return base64.b64decode(response.json()["data"][0]["b64_json"])


def spicy_render(prompt, images, client, max_charge, receipt=None, save=lambda: None, reserved="0", total_cap=None):
    """One Seedream edit. `receipt` is persisted by `save` before anything billable, so a job
    the provider already accepted is picked up again after a restart, not submitted twice."""
    receipt = receipt if receipt is not None else {}
    if not receipt.get("task_id"):
        if not receipt.get("request"):
            if not images:
                raise generation.BatchError("Seedream edit needs at least one image. Keep the style reference on.")
            uris = [client.upload(png)["uri"] for png in images]
            request = {"model": generation.MODEL, "input": {
                "image_urls": uris, "prompt": prompt, "aspect_ratio": "3:4", "resolution": "2k",
                "output_format": "png", "background": "opaque",
            }}
            quoted = client.api("POST", "/jobs/quote", payload=request)
            charge = generation.money(quoted["maxCharge"])
            if quoted.get("currency") != "USD" or charge > generation.money(max_charge):
                raise RunStop(f"SpicyAPI quote ${charge} is above the ${max_charge} per-image cap. Nothing was submitted.")
            if total_cap is not None and generation.money(reserved) + charge > generation.money(total_cap):
                raise RunStop(f"The ${total_cap} total cap is reached. Raise it in a new run to continue.")
            request.update(quoteId=quoted["quoteId"], expectedCost=quoted["estimatedCost"])
            receipt.update(request=request, max_charge=str(charge), idempotency_key=str(uuid.uuid4()))
            save()
        created = client.api("POST", "/jobs/createTask", payload=receipt["request"], idempotency_key=receipt["idempotency_key"])
        receipt["task_id"] = created["taskId"]
        save()
    record = client.poll(receipt["task_id"], 600, lambda _record: None)
    return client.storage("GET", record["output"]["assets"][0]["url"]).content


def caption_outfit(content, provider, key):
    """Ask a vision model for the outfit's label, prompt, category and body-zone tags."""
    with Image.open(io.BytesIO(content)) as image:
        image = image.convert("RGB")
        image.thumbnail((1024, 1024))
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=88)
    uri = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    model = pose_metadata.MODELS[provider]
    if provider == "xai":
        tags = {"type": "object", "additionalProperties": False, "required": list(OUTFIT_GROUPS),
                "properties": {group: {"type": "array", "items": {"type": "string"}} for group in OUTFIT_GROUPS}}
        schema = {"type": "object", "additionalProperties": False, "required": ["label", "prompt", "category", "tag_groups"],
                  "properties": {"label": {"type": "string"}, "prompt": {"type": "string"},
                                 "category": {"type": "string"}, "tag_groups": tags}}
        endpoint = "https://api.x.ai/v1/responses"
        payload = {"model": model, "store": False, "reasoning": {"effort": "low"}, "max_output_tokens": 1200,
                   "text": {"format": {"type": "json_schema", "name": "outfit_metadata", "strict": True, "schema": schema}},
                   "input": [{"role": "user", "content": [{"type": "input_image", "image_url": uri, "detail": "high"},
                                                          {"type": "input_text", "text": CAPTION_PROMPT}]}]}
    else:
        endpoint = "https://api.spicyapi.ai/v1/chat/completions"
        payload = {"model": model, "max_tokens": 600, "reasoning_effort": "none", "response_format": {"type": "json_object"},
                   "messages": [{"role": "user", "content": [{"type": "text", "text": CAPTION_PROMPT},
                                                             {"type": "image_url", "image_url": {"url": uri}}]}]}
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(endpoint, headers={"Authorization": f"Bearer {key}"}, json=payload,
                                    timeout=(10, 100), allow_redirects=False)
        if response.status_code != 200:
            raise generation.BatchError(f"{model} returned HTTP {response.status_code}. Check the provider key, access, and balance.")
        result = response.json()
        if provider == "xai":
            reply = next(c["text"] for item in result["output"] if item.get("type") == "message"
                         for c in item["content"] if c.get("type") == "output_text")
        else:
            reply = result["choices"][0]["message"]["content"]
        return json.loads(reply)
    except requests.RequestException:
        raise generation.BatchError(f"Could not reach {model}. Check the connection before retrying.") from None
    except (ValueError, KeyError, IndexError, TypeError, StopIteration):
        raise generation.BatchError(f"{model} returned an unreadable caption.") from None


def clean_metadata(raw):
    """Normalise a caption (from a model or the editor) into catalog fields."""
    if not isinstance(raw, dict):
        raise generation.BatchError("Unreadable outfit metadata.")
    label = " ".join(str(raw.get("label", "")).split())[:120]
    prompt = " ".join(str(raw.get("prompt", "")).split())[:600]
    if prompt and not prompt.lower().startswith("wearing "):
        prompt = "wearing " + prompt[0].lower() + prompt[1:]
    category = " ".join(str(raw.get("category", "")).split())[:40]
    groups = {}
    source = raw.get("tag_groups") if isinstance(raw.get("tag_groups"), dict) else {}
    for group in OUTFIT_GROUPS:
        values = source.get(group) or []
        tags = list(dict.fromkeys(" ".join(str(tag).lower().split())[:40] for tag in values if str(tag).strip()))[:12]
        if tags:
            groups[group] = tags
    extra = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    tags = sorted({tag for values in groups.values() for tag in values} |
                  {" ".join(str(tag).lower().split())[:40] for tag in extra if str(tag).strip()})[:60]
    return {"label": label, "prompt": prompt, "category": category, "tag_groups": groups, "tags": tags}


class OutfitStudio:
    def __init__(self, root=ROOT, spicy_factory=None, openai_render_fn=None, caption_fn=None):
        self.root = root
        self.home = root / ".cache/pose-studio/outfits"
        self.uploads = self.home / "uploads"
        self.runs = self.home / "runs"
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.runs.mkdir(parents=True, exist_ok=True)
        self.spicy_factory = spicy_factory or (lambda: generation.SpicyClient(generation.read_key()))
        self.openai_render = openai_render_fn or openai_render
        self.caption_fn = caption_fn or caption_outfit
        self.lock = threading.RLock()
        self.queue = []
        self.task = None
        self.caption_tasks = {}
        self.pause_requested = set()
        self.closing = False
        # Paid work never restarts by itself: whatever was queued or running is paused.
        for path in self.runs.glob("*/run.json"):
            run = json.loads(path.read_text())
            changed = False
            if run["status"] in ("queued", "running", "pausing"):
                run["status"], run["error"], changed = "paused", "Server stopped. Resume to continue.", True
            for item in run["items"]:
                if item["state"] in ACTIVE:
                    item["state"], changed = "waiting", True
            if run.get("caption", {}).get("running"):
                run["caption"]["running"], changed = False, True
            if changed:
                generation.write_json(path, run)

    # Storage ------------------------------------------------------------
    def reference_path(self):
        for path in (self.root / "assets/shipped/Outfits/img/preppy_O001.webp", self.root / "assets/Outfits/img/preppy_O001.webp", self.root / ".cache/outfit-baseline.png"):
            if path.is_file():
                return path
        return None

    def providers(self):
        ready = {}
        for name, check in (("openai", lambda: openai_key(self.root)), ("spicy", generation.read_key),
                            ("xai", lambda: pose_metadata.api_key("xai"))):
            try:
                check()
                ready[name] = True
            except (generation.BatchError, ValueError, OSError):
                ready[name] = False
        return ready

    def add_upload(self, content):
        if not content:
            raise generation.BatchError("Empty upload.")
        try:
            png = to_png(content)
        except (OSError, ValueError, Image.DecompressionBombError):
            raise generation.BatchError("Upload a PNG, JPEG or WebP image.") from None
        identity = generation.sha256(png)[:20]
        path = self.uploads / f"{identity}.png"
        if not path.is_file():
            generation.atomic_write(path, png)
        return {"id": identity}

    def upload_path(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{20}", identity):
            raise generation.BatchError("Unknown image.")
        path = self.uploads / f"{identity}.png"
        if not path.is_file():
            raise generation.BatchError("Unknown image. Choose the photos again.")
        return path

    def run_dir(self, run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise generation.BatchError("Unknown run.")
        path = self.runs / run_id
        if not (path / "run.json").is_file():
            raise generation.BatchError("Unknown run.")
        return path

    def run(self, run_id):
        return json.loads((self.run_dir(run_id) / "run.json").read_text())

    def change(self, run_id, mutate):
        """Read, mutate and write a run under the lock. The worker and the API share runs."""
        with self.lock:
            run = self.run(run_id)
            result = mutate(run)
            generation.write_json(self.run_dir(run_id) / "run.json", run)
            return run if result is None else result

    def item(self, run, index):
        if not isinstance(index, int) or not 0 <= index < len(run["items"]):
            raise generation.BatchError("Unknown outfit.")
        return run["items"][index]

    def file(self, run_id, index, kind):
        if kind not in ("source", "posed", "outfit"):
            raise generation.BatchError("Unknown image.")
        return self.run_dir(run_id) / kind / f"{int(index):04d}.png"

    # Runs ---------------------------------------------------------------
    def create_run(self, body):
        provider, mode, pose = body.get("provider"), body.get("mode"), body.get("pose", "a-pose")
        if provider not in ("openai", "spicy"):
            raise generation.BatchError("Choose OpenAI or SpicyAPI.")
        if mode not in ("photo", "text"):
            raise generation.BatchError("Choose photos or descriptions.")
        if mode == "photo" and pose not in (*POSES, *SINGLE_STEP):
            raise generation.BatchError("Choose A-pose, I-pose, skip posing, or flat lay.")
        entries = body.get("uploads" if mode == "photo" else "texts")
        if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_ITEMS:
            raise generation.BatchError(f"Select 1 to {MAX_ITEMS} items.")
        use_style = bool(body.get("use_style", True)) and self.reference_path() is not None
        if provider == "spicy" and mode == "text" and not use_style:
            raise generation.BatchError("Seedream needs an image. Keep the style reference on for text runs.")
        per_image, total = generation.money(body.get("max_charge", "0.06")), generation.money(body.get("total_cap", "3.00"))
        run_id = uuid.uuid4().hex
        folder = self.runs / run_id
        items = []
        sources = []
        for index, entry in enumerate(entries):
            item = {"index": index, "state": "waiting", "error": None, "posed": False, "outfit": False, "deleted": False,
                    "revision": 0, "requests": 0, "receipts": {}, "saved_as": None,
                    "label": "", "prompt": "", "category": "", "tags": [], "tag_groups": {}}
            if mode == "photo":
                if not isinstance(entry, dict):
                    raise generation.BatchError("Invalid photo list.")
                sources.append(self.upload_path(entry.get("id")))
                item["name"] = " ".join(str(entry.get("name") or f"Photo {index + 1}").split())[:120]
            else:
                text = " ".join(str(entry).split())
                if not 3 <= len(text) <= 1500:
                    raise generation.BatchError("Each description needs 3 to 1500 characters.")
                item["text"] = text
                item["name"] = text[:60]
                item["prompt"] = text if text.lower().startswith("wearing ") else "wearing " + text
            items.append(item)
        (folder / "source").mkdir(parents=True)
        for index, path in enumerate(sources):
            shutil.copyfile(path, folder / "source" / f"{index:04d}.png")
        run = {"id": run_id, "label": " ".join(str(body.get("label") or "Outfit run").split())[:100], "created": now(),
               "provider": provider, "mode": mode, "pose": pose if mode == "photo" else None, "use_style": use_style,
               "extra": " ".join(str(body.get("extra", "")).split())[:1000],
               "max_charge": str(per_image), "total_cap": str(total), "status": "paused", "error": None, "items": items}
        generation.write_json(folder / "run.json", run)
        self.enqueue(run_id)
        return self.public(self.run(run_id))

    def steps(self, run, item):
        """The paid steps an item still needs, in order."""
        steps = []
        if run["mode"] == "photo" and run["pose"] not in SINGLE_STEP and not item["posed"]:
            steps.append("pose")
        if not item["outfit"]:
            steps.append("extract" if run["mode"] == "photo" else "text")
        return steps

    def prompt_and_images(self, run, item, step):
        images = []
        if step == "pose":
            images.append(self.file(run["id"], item["index"], "source").read_bytes())
            prompt = (PROMPTS / "outfit-pose-normalize.txt").read_text().strip().replace("{pose}", POSES[run["pose"]])
        elif step == "extract":
            kind = "source" if run["pose"] in SINGLE_STEP else "posed"
            images.append(self.file(run["id"], item["index"], kind).read_bytes())
            template = "outfit-from-collage.txt" if run["pose"] == "collage" else "outfit-extract.txt"
            prompt = (PROMPTS / template).read_text().strip()
        else:
            garments = re.sub(r"^wearing\s+", "", item["text"], flags=re.I)
            prompt = (PROMPTS / "outfit-ghost-mannequin.txt").read_text().strip().replace("{outfit}", "The outfit: " + garments)
        if run["extra"]:
            prompt += " " + run["extra"]
        reference = self.reference_path() if run["use_style"] and step != "pose" else None
        if reference is not None:
            images.append(to_png(reference.read_bytes()))
            prompt += " " + STYLE_NOTE.format(n=len(images))
        return prompt, images

    def render(self, run, item, step):
        prompt, images = self.prompt_and_images(run, item, step)
        run_id, index = run["id"], item["index"]
        if run["provider"] == "openai":
            try:
                key = openai_key(self.root)
            except generation.BatchError as error:
                raise RunStop(str(error)) from None
            content = self.openai_render(prompt, images, key)
        else:
            try:
                client = self.spicy_factory()
            except (generation.BatchError, ValueError, OSError):
                raise RunStop("Set SPICY_API_KEY in the environment or the project .env.") from None
            receipt = dict(item["receipts"].get(step) or {})
            reserved = sum((generation.money(r["max_charge"]) for other in run["items"] for name, r in other["receipts"].items()
                            if r.get("max_charge") and not (other["index"] == index and name == step)), generation.money("0"))

            def save():
                self.change(run_id, lambda saved: saved["items"][index]["receipts"].__setitem__(step, dict(receipt)))

            content = spicy_render(prompt, images, client, run["max_charge"], receipt, save, str(reserved), run["total_cap"])
        target = self.file(run_id, index, "posed" if step == "pose" else "outfit")
        target.parent.mkdir(exist_ok=True)
        generation.atomic_write(target, fit_output(content))

    # Queue --------------------------------------------------------------
    def save_queue(self):
        generation.write_json(self.home / "queue.json", {"ids": self.queue})

    def enqueue(self, run_id):
        def mutate(run):
            if run["status"] not in ("running", "pausing"):
                run["status"], run["error"] = "queued", None
        self.change(run_id, mutate)
        with self.lock:
            self.pause_requested.discard(run_id)
            if run_id not in self.queue:
                self.queue.append(run_id)
            self.save_queue()
        self.kick()

    def kick(self):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # No event loop (tests, scripts): call work() directly.
        if self.task is None or self.task.done():
            self.task = loop.create_task(self.drain())

    async def drain(self):
        while not self.closing:
            with self.lock:
                if not self.queue:
                    return
                run_id = self.queue[0]
            await asyncio.to_thread(self.work, run_id)
            with self.lock:
                if run_id in self.queue:
                    self.queue.remove(run_id)
                self.save_queue()

    def work(self, run_id):
        """Run every remaining step of one run, an item at a time. Stops between items on pause."""
        self.change(run_id, lambda run: run.update(status="running", error=None))
        stop = None
        for index in range(len(self.run(run_id)["items"])):
            if self.closing or run_id in self.pause_requested:
                stop = "Paused."
                break
            run = self.run(run_id)
            item = run["items"][index]
            if item["deleted"] or item["state"] == "failed":
                continue
            try:
                for step in self.steps(run, item):
                    state = "posing" if step == "pose" else "extracting"
                    self.change(run_id, lambda saved: saved["items"][index].update(state=state, error=None))
                    self.render(self.run(run_id), self.run(run_id)["items"][index], step)

                    def done(saved, step=step):
                        entry = saved["items"][index]
                        entry["posed" if step == "pose" else "outfit"] = True
                        entry["requests"] += 1
                        entry["revision"] += 1
                    self.change(run_id, done)
                self.change(run_id, lambda saved: saved["items"][index].update(state="saved", error=None))
            except RunStop as error:
                self.change(run_id, lambda saved: saved["items"][index].update(state="waiting"))
                stop = str(error)
                break
            except generation.BatchError as error:
                message = str(error)
                self.change(run_id, lambda saved: saved["items"][index].update(state="failed", error=message))
            except Exception:
                self.change(run_id, lambda saved: saved["items"][index].update(
                    state="failed", error="Unexpected error. Retry this outfit."))
        self.pause_requested.discard(run_id)

        def finish(run):
            if stop:
                run["status"], run["error"] = "paused", None if stop == "Paused." else stop
            else:
                run["status"] = "completed"
        self.change(run_id, finish)

    def pause(self, run_id):
        def mutate(run):
            if run["status"] == "running":
                self.pause_requested.add(run_id)
                run["status"] = "pausing"
            elif run["status"] == "queued":
                with self.lock:
                    if run_id in self.queue:
                        self.queue.remove(run_id)
                    self.save_queue()
                run["status"] = "paused"
        return self.public(self.change(run_id, mutate))

    def resume(self, run_id):
        if self.run(run_id)["status"] not in ("paused", "completed"):
            raise generation.BatchError("This run is already queued or running.")
        self.enqueue(run_id)
        return self.public(self.run(run_id))

    # Items --------------------------------------------------------------
    def regenerate(self, run_id, index, step):
        def mutate(run):
            item = self.item(run, index)
            if item["state"] in ACTIVE:
                raise generation.BatchError("This outfit is being generated. Wait for it to finish.")
            redo_pose = step == "pose" and run["mode"] == "photo" and run["pose"] not in SINGLE_STEP
            if step not in ("pose", "extract") or (step == "pose" and not redo_pose):
                raise generation.BatchError("Choose what to regenerate.")
            if redo_pose:
                item["posed"] = False
                item["receipts"].pop("pose", None)
                self.file(run_id, index, "posed").unlink(missing_ok=True)
            item["outfit"] = False
            item["receipts"].pop("extract", None)
            item["receipts"].pop("text", None)
            self.file(run_id, index, "outfit").unlink(missing_ok=True)
            item.update(state="waiting", error=None, deleted=False, saved_as=None)
            item["revision"] += 1
        self.change(run_id, mutate)
        self.enqueue(run_id)
        return self.public(self.run(run_id))

    def set_deleted(self, run_id, index, deleted):
        def mutate(run):
            item = self.item(run, index)
            if item["state"] in ACTIVE:
                raise generation.BatchError("This outfit is being generated. Wait for it to finish.")
            item["deleted"] = bool(deleted)
        return self.public(self.change(run_id, mutate))

    def edit(self, run_id, index, body):
        fields = clean_metadata({"label": body.get("label", ""), "prompt": body.get("prompt", ""),
                                 "category": body.get("category", ""), "tag_groups": body.get("tag_groups", {}),
                                 "tags": [tag for tag in str(body.get("tags", "")).split(",")] if isinstance(body.get("tags"), str) else body.get("tags", [])})
        return self.public(self.change(run_id, lambda run: self.item(run, index).update(fields)))

    # Captions -----------------------------------------------------------
    def caption_targets(self, run, everything=False):
        return [item["index"] for item in run["items"]
                if item["outfit"] and not item["deleted"] and (everything or not item["label"])]

    def start_caption(self, run_id, provider, everything=False):
        if provider not in ("xai", "spicy"):
            raise generation.BatchError("Choose a caption model.")
        key = pose_metadata.api_key(provider)
        run = self.run(run_id)
        if run.get("caption", {}).get("running"):
            raise generation.BatchError("Captions are already running for this run.")
        targets = self.caption_targets(run, everything)
        if not targets:
            raise generation.BatchError("Every finished outfit already has a caption.")
        self.change(run_id, lambda saved: saved.update(caption={"running": True, "done": 0, "total": len(targets), "error": None}))
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.caption(run_id, targets, provider, key)
        else:
            self.caption_tasks[run_id] = loop.create_task(asyncio.to_thread(self.caption, run_id, targets, provider, key))
        return self.public(self.run(run_id))

    def caption(self, run_id, targets, provider, key):
        error = None
        for index in targets:
            if self.closing:
                break
            try:
                fields = clean_metadata(self.caption_fn(self.file(run_id, index, "outfit").read_bytes(), provider, key))
            except generation.BatchError as failure:
                error = str(failure)
                break

            def mutate(run, fields=fields, index=index):
                item = run["items"][index]
                if run["mode"] == "text":  # the written description is the prompt
                    fields = {**fields, "prompt": item["prompt"] or fields["prompt"]}
                item.update(fields)
                run["caption"]["done"] += 1
            self.change(run_id, mutate)
        self.change(run_id, lambda run: run["caption"].update(running=False, error=error))

    # Gallery ------------------------------------------------------------
    def galleries(self):
        names = []
        for path in sorted((self.root / "assets").glob("*/catalog.json")):
            name = path.parent.name
            if not name.lower().startswith("gallery") and GALLERY_NAME.fullmatch(name):
                try:
                    if json.loads(path.read_text()).get("outfit") is not None:
                        names.append(name)
                except (OSError, ValueError):
                    continue
        return names

    def save(self, run_id, body):
        name = str(body.get("gallery") or "My Outfits").strip()
        if not GALLERY_NAME.fullmatch(name) or name.lower().startswith("gallery") or name.lower() == "registry":
            raise generation.BatchError("Use a gallery name of letters, numbers, spaces, dots, dashes or underscores.")
        fallback = " ".join(str(body.get("category") or "Custom").split())[:40] or "Custom"
        run = self.run(run_id)
        wanted = body.get("indices")
        chosen = [item for item in run["items"] if item["outfit"] and not item["deleted"] and not item["saved_as"]
                  and (wanted is None or item["index"] in wanted)]
        if not chosen:
            raise generation.BatchError("No finished outfits left to save.")
        folder = self.root / "assets" / name
        catalog_path = folder / "catalog.json"
        catalog = json.loads(catalog_path.read_text()) if catalog_path.is_file() else {"schema_version": 1, "pose": [], "outfit": []}
        catalog.setdefault("pose", [])
        catalog.setdefault("outfit", [])
        (folder / "img").mkdir(parents=True, exist_ok=True)
        (folder / "thumbs").mkdir(exist_ok=True)
        taken = {entry.get("id") for entry in catalog["outfit"]}
        saved = {}
        for item in chosen:
            category = item["category"] or fallback
            slug = re.sub(r"[^a-z0-9]+", "", category.lower()) or "custom"
            number = 1
            while f"{slug}_O{number:03d}" in taken:
                number += 1
            outfit_id = f"{slug}_O{number:03d}"
            taken.add(outfit_id)
            label = item["label"] or item.get("text", "")[:120] or f"Outfit {item['index'] + 1}"
            prompt = item["prompt"] or "wearing " + label[0].lower() + label[1:]
            with Image.open(self.file(run_id, item["index"], "outfit")) as image:
                image = image.convert("RGB")
                image.save(folder / "img" / f"{outfit_id}.webp", "WEBP", quality=90, method=6)
                thumb = image.copy()
                thumb.thumbnail((512, 512))
                thumb.save(folder / "thumbs" / f"{outfit_id}.webp", "WEBP", quality=80, method=6)
            catalog["outfit"].append({"id": outfit_id, "label": label, "prompt": prompt, "category": category,
                                      "tags": item["tags"], "tag_groups": item["tag_groups"],
                                      "image": f"img/{outfit_id}.webp", "thumbnail": f"thumbs/{outfit_id}.webp"})
            saved[item["index"]] = {"gallery": name, "id": outfit_id}
        generation.write_json(catalog_path, catalog)

        def mutate(current):
            for index, where in saved.items():
                current["items"][index]["saved_as"] = where
        self.change(run_id, mutate)
        return {"saved": len(saved), "gallery": name, "count": len(catalog["outfit"]), "run": self.public(self.run(run_id))}

    # Views --------------------------------------------------------------
    def public(self, run):
        items = []
        for item in run["items"]:
            view = {key: value for key, value in item.items() if key != "receipts"}
            base = f"/media/outfit/{run['id']}/{item['index']}"
            view["urls"] = {"source": f"{base}/source" if run["mode"] == "photo" else None,
                            "posed": f"{base}/posed?v={item['revision']}" if item["posed"] else None,
                            "outfit": f"{base}/outfit?v={item['revision']}" if item["outfit"] else None}
            view["charged"] = str(sum((generation.money(r["max_charge"]) for r in item["receipts"].values() if r.get("task_id")),
                                      generation.money("0")))
            items.append(view)
        with self.lock:
            position = self.queue.index(run["id"]) if run["id"] in self.queue else None
        return {**{key: value for key, value in run.items() if key != "items"}, "items": items, "queue_position": position,
                "folder": str(self.run_dir(run["id"]))}

    def summaries(self):
        rows = []
        for path in self.runs.glob("*/run.json"):
            try:
                run = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            live = [item for item in run["items"] if not item["deleted"]]
            rows.append({"id": run["id"], "label": run["label"], "created": run["created"], "status": run["status"],
                         "mode": run["mode"], "total": len(live), "done": sum(item["outfit"] for item in live)})
        return sorted(rows, key=lambda row: row["created"], reverse=True)


# Routes -------------------------------------------------------------------
async def _read(request, limit):
    result = bytearray()
    async for chunk in request.content.iter_chunked(64 * 1024):
        result.extend(chunk)
        if len(result) > limit:
            raise generation.BatchError("Upload exceeds the size limit.")
    return bytes(result)


async def _json(request, limit=512 * 1024):
    body = json.loads(await _read(request, limit) or b"{}")
    if not isinstance(body, dict):
        raise generation.BatchError("Expected a JSON object.")
    return body


async def page(request):
    return web.FileResponse(Path(__file__).with_name("pose_studio_web") / "outfits.html")


async def outfit_config(request):
    studio = request.app[OUTFIT_STUDIO]
    return web.json_response({"providers": studio.providers(), "poses": list(POSES),
                              "style_reference": studio.reference_path() is not None,
                              "galleries": studio.galleries(), "runs": studio.summaries(),
                              "size": list(OUTPUT_SIZE), "max_items": MAX_ITEMS})


async def outfit_upload(request):
    studio = request.app[OUTFIT_STUDIO]
    content = await _read(request, MAX_UPLOAD)
    return web.json_response(await asyncio.to_thread(studio.add_upload, content))


async def outfit_runs(request):
    studio = request.app[OUTFIT_STUDIO]
    return web.json_response(studio.create_run(await _json(request)))


async def outfit_run(request):
    studio = request.app[OUTFIT_STUDIO]
    return web.json_response(studio.public(studio.run(request.match_info["id"])))


async def outfit_run_action(request):
    studio, run_id, action = request.app[OUTFIT_STUDIO], request.match_info["id"], request.match_info["action"]
    body = await _json(request)
    if action == "pause":
        return web.json_response(studio.pause(run_id))
    if action == "resume":
        return web.json_response(studio.resume(run_id))
    if action == "caption":
        return web.json_response(studio.start_caption(run_id, body.get("provider"), bool(body.get("all"))))
    return web.json_response(await asyncio.to_thread(studio.save, run_id, body))


async def outfit_item_action(request):
    studio, run_id, action = request.app[OUTFIT_STUDIO], request.match_info["id"], request.match_info["action"]
    index = int(request.match_info["index"])
    body = await _json(request)
    if action == "regenerate":
        return web.json_response(studio.regenerate(run_id, index, body.get("step", "extract")))
    if action in ("delete", "restore"):
        return web.json_response(studio.set_deleted(run_id, index, action == "delete"))
    return web.json_response(studio.edit(run_id, index, body))


async def outfit_media(request):
    studio = request.app[OUTFIT_STUDIO]
    path = studio.file(request.match_info["id"], int(request.match_info["index"]), request.match_info["kind"])
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path)


def register(app, root=ROOT, **kwargs):
    studio = app[OUTFIT_STUDIO] = OutfitStudio(root, **kwargs)
    app.router.add_get("/outfits", page)
    app.router.add_get("/api/outfits", outfit_config)
    app.router.add_put("/api/outfits/uploads", outfit_upload)
    app.router.add_post("/api/outfits/runs", outfit_runs)
    app.router.add_get("/api/outfits/runs/{id}", outfit_run)
    app.router.add_post("/api/outfits/runs/{id}/{action:pause|resume|caption|save}", outfit_run_action)
    app.router.add_post(r"/api/outfits/runs/{id}/items/{index:\d+}/{action:regenerate|delete|restore|edit}", outfit_item_action)
    app.router.add_get(r"/media/outfit/{id}/{index:\d+}/{kind:source|posed|outfit}", outfit_media)

    async def shutdown(application):
        studio.closing = True
        if studio.task and not studio.task.done():
            print("Finishing the current outfit before stopping. The run can be resumed.", flush=True)
            await studio.task
        await asyncio.gather(*studio.caption_tasks.values(), return_exceptions=True)

    app.on_shutdown.append(shutdown)
