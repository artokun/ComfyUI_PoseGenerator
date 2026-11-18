"""Shared Studio tag vocabulary and the constrained vision response format."""
import hashlib
import json

try:
    from .generate_pose_references import BatchError
except ImportError:
    from generate_pose_references import BatchError

DEFAULT = {"Camera position": ["front view", "three-quarter view", "side view", "rear view", "high-angle view", "low-angle view"],
           "Character stance": ["standing", "sitting", "kneeling", "crouching", "lying", "walking", "running", "jumping", "leaning", "reclining", "bending", "lunging", "balancing", "crawling", "hanging"],
           "Character count": ["solo", "pair"],
           "Gesture": ["arms raised", "arms crossed", "hands on hips", "hands on knee", "reaching", "pointing", "holding hands"]}


def validate(groups):
    if not isinstance(groups, dict) or len(groups) > 16:
        raise BatchError("Use up to 16 named tag rows.")
    result = {}
    seen = set()
    for name, tags in groups.items():
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 48 or not name.isprintable() or name.strip().casefold() in seen:
            raise BatchError("Give each tag row a unique name of 1–48 characters.")
        if not isinstance(tags, list) or len(tags) > 64:
            raise BatchError("Each row accepts up to 64 tags.")
        clean = []
        for tag in tags:
            if not isinstance(tag, str) or not 1 <= len(tag.strip()) <= 48 or not tag.isprintable() or "," in tag:
                raise BatchError("Tags must be 1–48 characters and cannot contain commas.")
            tag = tag.strip().lower()
            if tag not in clean:
                clean.append(tag)
        seen.add(name.strip().casefold())
        result[name.strip()] = clean
    return result


def version(groups):
    return hashlib.sha256(json.dumps(groups, sort_keys=True).encode()).hexdigest()


def select(raw, groups):
    if not isinstance(raw, dict) or set(raw) != set(groups):
        raise BatchError("The model returned different tag rows than requested.")
    result = {}
    for name, allowed in groups.items():
        values = raw[name]
        if not isinstance(values, list) or any(value not in allowed for value in values):
            raise BatchError(f"The model returned an unlisted tag for {name}.")
        result[name] = [tag for tag in allowed if tag in values]
    return result


def response_schema(groups, angles):
    def obj(fields):
        return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}
    def choice(options):
        return {"type": "string", "enum": list(options)}
    rows = {name: {"type": "array", "items": choice(tags)} if tags else {"type": "array", "items": {"type": "string"}, "maxItems": 0}
            for name, tags in groups.items()}
    normal = obj({"label": {"type": "string"}, "count": choice(["solo", "pair"]), "crop": choice(["upper", "full"]),
                  "expressive": {"type": "boolean"}, "angle": choice(angles), "tag_groups": obj(rows)})
    review = obj({"review": {"type": "boolean", "enum": [True]}})
    return obj({"pose": {"anyOf": [normal, review]}})
