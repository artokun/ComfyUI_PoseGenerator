"""Describe a generated drawing and map the result to the local pose catalog."""
import base64
import io
import json

from PIL import Image
import requests

try:
    from . import generate_pose_references as generation, pose_tags
except ImportError:
    import generate_pose_references as generation
    import pose_tags

MODELS = {"xai": "grok-4.7", "spicy": "nvidia/nemotron-3.5-super-vl/chat"}
CATEGORIES = {"full_single": "Full body", "full_sexy": "Expressive full body",
              "upper80v2": "Upper body", "pair60": "Two people"}
ANGLES = {"front": "front view", "side": "side view", "diag": "three-quarter view",
          "low": "low-angle view", "high": "high-angle view", "rear": "rear view"}
DEFAULT_PROMPT = """Write an original English caption for this mannequin pose in a figure-drawing catalog.
Use one short, sentence-case phrase, usually 5–18 words. Start with the posture or action. Add the one or two details that distinguish the pose: where the weight rests, how the torso turns, or what the hands or legs are doing.
For a pair, start with “Two people” and describe how the figures relate. For a cropped drawing, describe the visible gesture. Use left or right only when the figure’s own side is clear.
Keep the wording concrete and neutral. Leave out the camera angle, drawing style, identity, clothing, setting, and mood. Describe only what is visible.

Examples of phrasing:
Kneeling with one forearm balanced across the raised thigh
Two people standing shoulder to shoulder with their outer arms raised"""

OUTPUT_FORMAT = """Inspect the single supplied image and apply the caption instructions to the label.
Return JSON with a single "pose" object. It contains label, count (solo or pair),
crop (upper or full), expressive (boolean), angle (front, side, diag for three-quarter,
low, high, or rear), and tag_groups. Use expressive for a distinctly stylized full-body gesture.
For tag_groups return every supplied row with an array of applicable values exactly as written.
Use an empty array where no tag applies. Keep count and camera tags consistent with the pose.
If the figures or view cannot be classified, return {"pose":{"review":true}}.
The permitted rows and values follow."""


def normalize_prompt(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 5000:
        raise generation.BatchError("Caption prompt must contain 1–5000 characters.")
    return value.strip()


def prompt_digest(prompt):
    return generation.sha256(prompt.encode("utf-8"))


def api_key(provider):
    if provider == "xai":
        return generation.read_key(("GROK_API_KEY", "XAI_API_KEY"))
    if provider == "spicy":
        return generation.read_key()
    raise generation.BatchError("Unknown metadata provider.")


def catalog_fields(raw, number, groups):
    if not isinstance(raw, dict):
        raise generation.BatchError("The model returned invalid pose metadata.")
    if raw.get("review") is True:
        return {"review": True}
    label, angle = raw.get("label"), raw.get("angle")
    count, crop = raw.get("count"), raw.get("crop")
    if not (isinstance(label, str) and 3 <= len(label) <= 150 and label.isprintable()
            and isinstance(angle, str) and angle in ANGLES and count in ("solo", "pair")
            and crop in ("upper", "full") and isinstance(raw.get("expressive"), bool)):
        raise generation.BatchError("The model returned invalid pose metadata.")
    label = label.strip().rstrip(".").strip()
    if len(label) < 3:
        raise generation.BatchError("The model returned an empty pose label.")
    selected = pose_tags.select(raw.get("tag_groups"), groups)
    if count == "pair":
        prefix = "pair60"
    elif crop == "upper":
        prefix = "upper80v2"
    elif raw["expressive"]:
        prefix = "full_sexy"
    else:
        prefix = "full_single"
    return {"id": f"{prefix}_P{number:03d}_{angle}", "label": label[0].upper() + label[1:],
            "prompt": f"{label[0].lower() + label[1:]}, {ANGLES[angle]}",
            "category": CATEGORIES[prefix], "angle": ANGLES[angle],
            "tag_groups": selected, "tags": list(dict.fromkeys(t for row in selected.values() for t in row))}


def describe(content, provider, key, caption_prompt, groups):
    instructions = normalize_prompt(caption_prompt) + "\n\n" + OUTPUT_FORMAT + "\n" + json.dumps(groups, ensure_ascii=False)
    with Image.open(io.BytesIO(content)) as image:
        image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        rgb = Image.new("RGB", image.size, "white")
        rgba = image.convert("RGBA")
        rgb.paste(rgba, mask=rgba.getchannel("A"))
        encoded = io.BytesIO()
        rgb.save(encoded, format="JPEG", quality=88)
    uri = "data:image/jpeg;base64," + base64.b64encode(encoded.getvalue()).decode("ascii")
    model = MODELS[provider]
    if provider == "xai":
        endpoint = "https://api.x.ai/v1/responses"
        payload = {"model": model, "store": False, "reasoning": {"effort": "low"},
                   "max_output_tokens": 1800, "text": {"format": {"type": "json_schema", "name": "pose_metadata",
                       "strict": True, "schema": pose_tags.response_schema(groups, ANGLES)}},
                   "input": [{"role": "user", "content": [{"type": "input_image", "image_url": uri, "detail": "high"},
                             {"type": "input_text", "text": instructions}]}]}
    else:
        endpoint = "https://api.spicyapi.ai/v1/chat/completions"
        payload = {"model": model, "max_tokens": 600, "reasoning_effort": "none",
                   "response_format": {"type": "json_object"}, "messages": [
                       {"role": "user", "content": [{"type": "text", "text": instructions},
                        {"type": "image_url", "image_url": {"url": uri}}]}]}
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(endpoint, headers={"Authorization": f"Bearer {key}"},
                                    json=payload, timeout=(10, 100), allow_redirects=False)
        if response.status_code != 200:
            raise generation.BatchError(f"{model} returned HTTP {response.status_code}. Check the provider key, access, and balance.")
        result = response.json()
        if provider == "xai":
            reply = next(c["text"] for item in result["output"] if item.get("type") == "message"
                         for c in item["content"] if c.get("type") == "output_text")
        else:
            reply = result["choices"][0]["message"]["content"]
        return json.loads(reply)["pose"]
    except requests.RequestException:
        raise generation.BatchError(f"Could not reach {model}. Check the connection before retrying.") from None
    except (ValueError, KeyError, IndexError, TypeError, StopIteration):
        raise generation.BatchError(f"{model} returned an unreadable metadata response.") from None
