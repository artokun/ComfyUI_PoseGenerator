import json
import hashlib
from pathlib import Path

import pytest
import torch
from PIL import Image

from pose_generator.library import catalog, choices, image_path
from pose_generator.nodes import PoseOutfitReference
from pose_generator.prompts import DEFAULT_TEMPLATE, render_prompt


def test_images_and_prompt_match_selected_references(local_library):
    node = PoseOutfitReference()
    pose, outfit, prompt, pose_text, outfit_text = node.generate(
        "pose-a", "outfit-a", "a traveler", DEFAULT_TEMPLATE, "soft lighting"
    )
    assert pose.shape == outfit.shape == (1, 8, 12, 3)
    assert pose.dtype == torch.float32
    assert pose[0, 0, 0].tolist() == [1, 0, 0]
    assert outfit[0, 0, 0].tolist() == [0, 1, 0]
    assert prompt == "a traveler, standing, front view, wearing a blue jacket, soft lighting"
    assert pose_text == "standing, front view"
    assert outfit_text == "wearing a blue jacket"


def test_optional_selections_and_overrides(local_library):
    output = PoseOutfitReference().generate("None", "None", "a traveler", DEFAULT_TEMPLATE, "")
    assert output[0].shape == output[1].shape == (1, 1, 1, 3)
    assert output[2:] == ("a traveler", "", "")
    output = PoseOutfitReference().generate("pose-a", "outfit-a", "a traveler", DEFAULT_TEMPLATE, "", "running", "wearing boots")
    assert output[2] == "a traveler, running, wearing boots"


def test_no_recursive_interpolation_or_execution():
    assert render_prompt("{subject}, {{literal}}, {pose}", {"subject": "{outfit}", "pose": "standing"}) == "{outfit}, {literal}, standing"
    assert render_prompt("({pose}:1.2)\n{extra}", {"pose": "standing", "extra": "soft light"}) == "(standing:1.2)\nsoft light"


@pytest.mark.parametrize("template", ["{missing}", "{subject.__class__}", "{subject[0]}", "{pose!r}", "{pose:>10}", "{", "}"])
def test_invalid_template_fields_are_rejected(template):
    with pytest.raises(ValueError):
        render_prompt(template, {})


def test_missing_library_is_loadable_with_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setenv("POSE_GENERATOR_ASSETS", str(tmp_path))
    assert choices("pose") == ["None"]
    message = PoseOutfitReference.VALIDATE_INPUTS("missing", "None", DEFAULT_TEMPLATE)
    assert "Install the assets" in message


def test_path_traversal_and_external_symlinks_are_rejected(local_library, tmp_path_factory):
    with pytest.raises(ValueError):
        image_path("../secret.png")
    external = tmp_path_factory.mktemp("external") / "secret.png"
    Image.new("RGB", (1, 1)).save(external)
    (local_library / "img/link.png").symlink_to(external)
    with pytest.raises(ValueError):
        image_path("img/link.png")
    with pytest.raises(ValueError):
        image_path("catalog.json")


def test_missing_image_fails_instead_of_silently_substituting(local_library):
    (local_library / "img/pose-a.png").unlink()
    with pytest.raises(ValueError, match="Missing reference"):
        PoseOutfitReference().generate("pose-a", "None", "", DEFAULT_TEMPLATE, "")


def test_cache_changes_when_image_or_catalog_changes(local_library):
    first = PoseOutfitReference.IS_CHANGED("pose-a", "outfit-a")
    Image.new("RGB", (12, 8), "blue").save(local_library / "img/pose-a.png")
    second = PoseOutfitReference.IS_CHANGED("pose-a", "outfit-a")
    assert first != second
    path = local_library / "catalog.json"
    data = json.loads(path.read_text())
    data["pose"][0]["prompt"] = "sitting"
    path.write_text(json.dumps(data))
    assert catalog()["pose"]["pose-a"]["prompt"] == "sitting"
    assert PoseOutfitReference.IS_CHANGED("pose-a", "outfit-a") != second


def test_transparency_composited_on_white(local_library):
    Image.new("RGBA", (12, 8), (0, 0, 0, 0)).save(local_library / "img/pose-a.png")
    output = PoseOutfitReference().generate("pose-a", "None", "", DEFAULT_TEMPLATE, "")
    assert torch.all(output[0] == 1)


def test_optimized_gallery_activates_only_after_catalog_is_ready(tmp_path, monkeypatch):
    from pose_generator import library

    original = tmp_path / "gallery"
    optimized = tmp_path / "gallery-webp"
    monkeypatch.setattr(library, "DEFAULT_ROOT", original)
    monkeypatch.delenv("POSE_GENERATOR_ASSETS", raising=False)
    optimized.mkdir()
    (optimized / "optimization.json").write_text('{"complete": false}')
    assert library.library_root() == original
    (optimized / "catalog.json").write_text('{"schema_version": 1}')
    assert library.library_root() == optimized
    monkeypatch.setenv("POSE_GENERATOR_ASSETS", str(original))
    assert library.library_root() == original


def test_actual_downloaded_catalog_when_present(monkeypatch):
    root = Path(__file__).resolve().parents[1] / "assets/gallery"
    if not (root / "catalog.json").is_file():
        pytest.skip("Optional gallery is not installed on this runner")
    monkeypatch.setenv("POSE_GENERATOR_ASSETS", str(root))
    assert len(catalog()["pose"]) == 1439
    assert len(catalog()["outfit"]) == 1407
    for kind in ("pose", "outfit"):
        for entry in catalog()[kind].values():
            assert entry["prompt"].isascii()
            assert image_path(entry["image"]).is_file()
            assert image_path(entry["thumbnail"]).is_file()
    output = PoseOutfitReference().generate("full_single_P015_front", "SPR-0001", "a person", DEFAULT_TEMPLATE, "studio lighting")
    assert output[0].shape[-1] == output[1].shape[-1] == 3
    assert "contrapposto" in output[2]
    assert "wearing ivory" in output[2]


def test_named_galleries_resolve_collisions_recaptions_and_depth(local_library, monkeypatch):
    from pose_generator import library
    from shutil import copytree
    primary = local_library / "collections" / "gallery"
    primary.mkdir(parents=True)
    for name in ("img", "thumbs"):
        copytree(local_library / name, primary / name)
    (primary / "catalog.json").write_bytes((local_library / "catalog.json").read_bytes())
    monkeypatch.setenv("POSE_GENERATOR_ASSETS", str(primary))
    monkeypatch.setattr(library, "DEFAULT_ROOT", primary)
    doc = json.loads((local_library / "catalog.json").read_text())
    folder = primary.parent / "gallery-studio" / "example-run"
    folder.mkdir(parents=True)
    (folder / "studio.json").write_text(json.dumps({"label": "Instagram Poses"}))
    row = {**doc["pose"][0], "image": "mannequin.png", "thumbnail": "mannequin.png",
           "tag_groups": {"Character stance": ["standing"]}}
    Image.new("RGB", (12, 8), "blue").save(folder / "mannequin.png")
    digest = hashlib.sha256((folder / "mannequin.png").read_bytes()).hexdigest()
    (folder / "batch.json").write_text(json.dumps({"jobs": {"image.jpg": {
        "id": "stable", "downloaded": True, "output": "mannequin.png"}}}))
    (folder / "catalog.json").write_text(json.dumps({**doc, "pose": [row]}))
    retry = folder.with_name("retry")
    retry.mkdir()
    (retry / "studio.json").write_text('{"regenerated_from":{"id":"example-run"}}')
    (retry / "catalog.json").write_text(json.dumps(doc))
    galleries, entries = library.gallery_index()
    assert [g["label"] for g in galleries] == ["Original gallery", "Instagram Poses"]
    assert set(entries["outfit"]) == {"outfit-a"}
    identity = next(key for key in choices("pose") if key.startswith("@studio."))
    assert library.selected("pose", identity)["catalog_id"] == "pose-a"
    node = PoseOutfitReference()
    args = (identity, "None", "", "{pose}", "")
    assert node.generate(*args)[0][0, 0, 0].tolist() == [0, 0, 1]
    with pytest.raises(ValueError, match="depth map"):
        node.generate(*args, pose_image_mode="Saved depth")
    (folder / "depth").mkdir()
    Image.new("RGB", (12, 8), "white").save(folder / "depth" / (digest + ".png"))
    assert torch.all(node.generate(*args, pose_image_mode="Saved depth")[0] == 1)
    (folder / "studio.json").write_text(json.dumps({"label": "Renamed"}))
    (folder / "catalog.json").write_text(json.dumps({**doc, "pose": [{**row, "id": "new-angle", "prompt": "reaching up"}]}))
    assert library.selected("pose", identity)["prompt"] == "reaching up"
    assert library.gallery_index()[0][1]["label"] == "Renamed"
