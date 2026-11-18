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
    )[:5]
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
    assert output[2:5] == ("a traveler", "", "")
    assert output[5:] == (None, None, None, 0, 0)
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


def test_saved_depth_beside_the_drawing_in_the_original_gallery(local_library):
    # The original gallery stores depth maps as img/depth/<drawing name>, not
    # Pose Studio's depth/<sha256>.png. Both layouts must work.
    node = PoseOutfitReference()
    args = ("pose-a", "outfit-a", "a traveler", DEFAULT_TEMPLATE, "soft lighting")
    with pytest.raises(ValueError, match="depth map"):
        node.generate(*args, pose_image_mode="Saved depth")
    (local_library / "img" / "depth").mkdir()
    Image.new("RGB", (12, 8), (0, 0, 255)).save(local_library / "img" / "depth" / "pose-a.png")
    pose = node.generate(*args, pose_image_mode="Saved depth")[0]
    assert pose[0, 0, 0].tolist() == [0, 0, 1]
    # Drawing mode still returns the drawing itself.
    assert node.generate(*args, pose_image_mode="Drawing")[0][0, 0, 0].tolist() == [1, 0, 0]


def test_studio_depth_wins_over_a_file_beside_the_drawing(local_library):
    node = PoseOutfitReference()
    args = ("pose-a", "outfit-a", "a traveler", DEFAULT_TEMPLATE, "soft lighting")
    drawing = local_library / "img" / "pose-a.png"
    digest = hashlib.sha256(drawing.read_bytes()).hexdigest()
    (local_library / "img" / "depth").mkdir()
    Image.new("RGB", (12, 8), (0, 0, 255)).save(local_library / "img" / "depth" / "pose-a.png")
    (local_library / "depth").mkdir()
    Image.new("RGB", (12, 8), (255, 255, 255)).save(local_library / "depth" / f"{digest}.png")
    assert torch.all(node.generate(*args, pose_image_mode="Saved depth")[0] == 1)


def test_fresh_install_lists_only_bundled_galleries(tmp_path, monkeypatch):
    # A Registry install has no original gallery, only the bundled folder galleries.
    from pose_generator import library
    primary = tmp_path / "assets" / "gallery"  # standard location, never created
    monkeypatch.delenv("POSE_GENERATOR_ASSETS", raising=False)
    monkeypatch.setattr(library, "DEFAULT_ROOT", primary)
    library._read_catalog.cache_clear()
    bundled = tmp_path / "assets" / "shipped" / "Instagram Poses"
    (bundled / "img").mkdir(parents=True)
    Image.new("RGB", (12, 8), "blue").save(bundled / "img" / "p.webp")
    row = {"id": "p", "label": "Standing", "prompt": "standing", "category": "Full body",
           "image": "img/p.webp", "thumbnail": "img/p.webp", "angle": "front view"}
    (bundled / "catalog.json").write_text(json.dumps({"schema_version": 1, "pose": [row], "outfit": []}))
    galleries, entries = library.gallery_index()
    assert [g["label"] for g in galleries] == ["Instagram Poses"]
    identity = next(key for key in choices("pose") if key != "None")
    pose = PoseOutfitReference().generate(identity, "None", "a", DEFAULT_TEMPLATE, "")[0]
    assert pose[0, 0, 0].tolist() == pytest.approx([0, 0, 1], abs=0.02)  # lossy WebP
    library._read_catalog.cache_clear()


def test_folder_gallery_can_carry_outfits(tmp_path, monkeypatch):
    # Users' own outfits go in a folder gallery (assets/<name>/catalog.json "outfit"),
    # not in the original gallery's folder.
    from pose_generator import library
    monkeypatch.delenv("POSE_GENERATOR_ASSETS", raising=False)
    monkeypatch.setattr(library, "DEFAULT_ROOT", tmp_path / "assets" / "gallery")
    library._read_catalog.cache_clear()
    mine = tmp_path / "assets" / "My Outfits"
    (mine / "img").mkdir(parents=True)
    Image.new("RGB", (12, 8), (0, 255, 0)).save(mine / "img" / "o.png")
    row = {"id": "o", "label": "Green coat", "prompt": "wearing a green coat", "category": "Coats",
           "image": "img/o.png", "thumbnail": "img/o.png"}
    (mine / "catalog.json").write_text(json.dumps({"schema_version": 1, "pose": [], "outfit": [row]}))
    assert "My Outfits" not in [g["label"] for g in library.gallery_index()[0]]  # no poses: not a pose gallery
    outfit_id = next(key for key in choices("outfit") if key != "None")
    _, outfit, prompt, _, outfit_text = PoseOutfitReference().generate("None", outfit_id, "a", DEFAULT_TEMPLATE, "")[:5]
    assert outfit[0, 0, 0].tolist() == [0, 1, 0]
    assert outfit_text == "wearing a green coat"
    library._read_catalog.cache_clear()


H3 = "<Picture {n}>"


def test_subject_first_numbers_pictures_and_writes_an_edit_instruction(local_library):
    photo = torch.rand(2, 600, 400, 3)
    output = PoseOutfitReference().generate("pose-a", "outfit-a", "a traveler", DEFAULT_TEMPLATE, "soft lighting",
                                            picture_tag=H3, megapixels=1.0, subject_image=photo)
    assert output[2] == (
        "Edit <Picture 1>: show a traveler from <Picture 1>, keeping their face, hair and body, "
        "in the exact pose of the mannequin drawing in <Picture 2> (copy only the pose, not its look): standing, front view, "
        "wearing the outfit shown in <Picture 3> (only the clothes, not its background): a blue jacket, soft lighting"
    )
    picture_1, picture_2, picture_3, width, height = output[5:]
    assert picture_1.shape == (1, height, width, 3) and width % 32 == height % 32 == 0
    assert abs(width * height - 1_000_000) < 80_000 and abs(width / height - 400 / 600) < 0.05
    assert picture_2[0, 0, 0].tolist() == [1, 0, 0] and picture_3[0, 0, 0].tolist() == [0, 1, 0]


def test_pose_first_edits_the_drawing_and_skips_missing_pictures(local_library):
    photo = torch.rand(1, 64, 64, 3)
    node = PoseOutfitReference()
    output = node.generate("pose-a", "None", "a traveler", DEFAULT_TEMPLATE, "", picture_tag="image {n}",
                           edit_target="Pose", reference_text="Image only", subject_image=photo)
    assert output[2] == ("Edit image 1: replace the mannequin drawing with a real photo of a traveler from image 2, "
                         "keeping their face, hair and body, in exactly the same pose, framing and camera angle as the mannequin drawing in image 1 "
                         "(a real human body with natural skin, no mannequin joints), wearing the same clothes as in image 2")
    assert output[5][0, 0, 0].tolist() == [1, 0, 0]
    assert torch.equal(output[6], photo[:1])
    assert output[7] is None
    # No subject photo: the outfit is the only picture, so it is picture 1 and nothing is "edited".
    output = node.generate("None", "outfit-a", "a traveler", DEFAULT_TEMPLATE, "", picture_tag=H3)
    assert output[2] == "a traveler, wearing the outfit shown in <Picture 1> (only the clothes, not its background): a blue jacket"
    assert output[6] is None and output[7] is None


def test_empty_picture_tag_keeps_the_plain_prompt_and_bad_tags_are_rejected(local_library):
    photo = torch.rand(1, 64, 64, 3)
    output = PoseOutfitReference().generate("pose-a", "outfit-a", "a traveler", DEFAULT_TEMPLATE, "", subject_image=photo)
    assert output[2] == "a traveler, standing, front view, wearing a blue jacket"
    assert output[5] is not None  # pictures are still handed out in order
    message = PoseOutfitReference.VALIDATE_INPUTS("None", "None", DEFAULT_TEMPLATE, "Picture")
    assert "{n}" in message


def test_new_widgets_and_outputs_are_appended_for_saved_workflows():
    optional = list(PoseOutfitReference.INPUT_TYPES()["optional"])
    assert optional[:3] == ["pose_override", "outfit_override", "pose_image_mode"]
    assert optional[3:] == ["picture_tag", "edit_target", "megapixels", "reference_text", "location", "subject_image"]
    assert PoseOutfitReference.RETURN_NAMES[:5] == ("pose_image", "outfit_image", "positive_prompt", "pose_text", "outfit_text")


def test_new_nodes_edit_the_pose_by_default():
    assert PoseOutfitReference.INPUT_TYPES()["optional"]["edit_target"] == (["Pose", "Subject"], PoseOutfitReference.INPUT_TYPES()["optional"]["edit_target"][1])
    assert PoseOutfitReference.INPUT_TYPES()["optional"]["edit_target"][1]["default"] == "Pose"


def test_location_points_at_the_subject_photo_only_when_the_pose_is_edited(local_library):
    photo = torch.rand(1, 64, 64, 3)
    node = PoseOutfitReference()
    args = ("pose-a", "None", "a traveler", "{edit}|{location}", "")
    def prompt(**kwargs):
        return node.generate(*args, picture_tag=H3, **kwargs)[2].partition("|")[2]
    assert prompt(edit_target="Pose", location="From the subject photo", subject_image=photo) == "in the same location, background and lighting as <Picture 2>"
    assert prompt(edit_target="Subject", location="From the subject photo", subject_image=photo) == ""  # the photo is picture 1
    assert prompt(edit_target="Pose", location="From the subject photo") == ""  # no photo to point at
    assert prompt(edit_target="Pose", location="Plain studio", subject_image=photo) == "on a plain white studio background"
    assert prompt(edit_target="Pose", location="Custom", subject_image=photo) == ""
    assert node.generate(*args, location="Plain studio", subject_image=photo)[2] == "|"  # picture references off


def test_old_templates_still_get_the_edit_opener_and_the_location(local_library):
    photo = torch.rand(1, 64, 64, 3)
    node = PoseOutfitReference()
    old = "{subject}, {pose}, {outfit}, {extra}"
    prompt = node.generate("pose-a", "None", "Celia", old, "soft light", picture_tag=H3, edit_target="Pose",
                           location="From the subject photo", reference_text="Image only", subject_image=photo)[2]
    assert prompt.startswith("Edit <Picture 1>: replace the mannequin drawing with a real photo of Celia from <Picture 2>")
    assert prompt.endswith("in the same location, background and lighting as <Picture 2>, soft light")
    custom = node.generate("pose-a", "None", "Celia", "portrait of {subject}", "", picture_tag=H3, edit_target="Pose",
                           location="Plain studio", subject_image=photo)[2]
    assert custom.endswith("on a plain white studio background")
    assert node.generate("pose-a", "None", "Celia", old, "", subject_image=photo)[2] == "Celia, standing, front view"  # references off


def test_shipped_and_local_galleries_share_ids_and_the_local_folder_wins(tmp_path, monkeypatch):
    from pose_generator import library
    monkeypatch.delenv("POSE_GENERATOR_ASSETS", raising=False)
    monkeypatch.setattr(library, "DEFAULT_ROOT", tmp_path / "assets" / "gallery")
    library._read_catalog.cache_clear()
    def gallery(folder, colour, label):
        (folder / "img").mkdir(parents=True)
        Image.new("RGB", (12, 8), colour).save(folder / "img" / "p.png")
        row = {"id": "p", "label": label, "prompt": "standing", "category": "Full body", "image": "img/p.png", "thumbnail": "img/p.png"}
        (folder / "catalog.json").write_text(json.dumps({"schema_version": 1, "pose": [row], "outfit": []}))
    gallery(tmp_path / "assets/shipped/Studio Set", "blue", "shipped")
    gallery(tmp_path / "assets/shipped/Beach Set", "green", "beach")
    gallery(tmp_path / "assets/Studio Set", "red", "local")
    galleries, entries = library.gallery_index()
    assert [g["label"] for g in galleries] == ["Studio Set", "Beach Set"]
    assert entries["pose"]["@folder.Studio%20Set:p"]["label"] == "local"
    assert "@folder.Beach%20Set:p" in entries["pose"]
    library._read_catalog.cache_clear()
