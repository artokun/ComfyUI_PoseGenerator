import pytest
import yara

from scripts.check_release import REQUIRED, ROOT, shipped_paths


def test_package_keeps_runtime_and_excludes_private_or_development_files(tmp_path):
    (tmp_path / ".comfyignore").write_text((ROOT / ".comfyignore").read_text())
    (tmp_path / ".gitignore").write_text((ROOT / ".gitignore").read_text())
    for relative in REQUIRED | {".env", ".env.local", "assets/gallery/img/test.png", "assets/gallery-webp/img/test.webp", "scripts/example.py", "tests/example.py", ".cache/secret.txt", ".playwright-mcp/console.log"}:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    assert set(shipped_paths(tmp_path, working_tree=True)) == REQUIRED


def test_yara_rules_actually_detect_known_registry_patterns():
    rules = yara.compile(filepath=str(ROOT / "security/registry.yar"))
    triggers = [b'subprocess.run(["example"])', b'os.system("example")', b'<svg onload="example">',
                b'sock.connect (address)', b'sock.recv(10)', b'client = aiohttp.ClientSession()']
    for trigger in triggers:
        assert rules.match(data=trigger), trigger
    assert not rules.match(data=b'image.addEventListener("load", callback)')


def test_english_descriptions_have_expected_coverage():
    import json
    import pytest
    path = ROOT / "scripts/data/pose_english.json"
    if not path.is_file():
        pytest.skip("Optional local research translations are not distributed")
    data = json.loads(path.read_text())
    assert set(data) == {"full_single", "full_sexy", "upper80v2", "pair60"}
    assert all(text.isascii() and len(text) > 10 for group in data.values() for text in group.values())


def test_bundled_galleries_ship_but_original_gallery_and_other_assets_do_not(tmp_path):
    (tmp_path / ".comfyignore").write_text((ROOT / ".comfyignore").read_text())
    (tmp_path / ".gitignore").write_text((ROOT / ".gitignore").read_text())
    bundled = {"assets/shipped/Instagram Poses/catalog.json", "assets/shipped/Instagram Poses/img/a.webp",
               "assets/shipped/Instagram Poses/img/depth/a.webp", "assets/shipped/OF poses/thumbs/b.webp",
               "assets/shipped/Outfits/catalog.json", "assets/shipped/Outfits/img/preppy_O001.webp"}
    elsewhere = {"assets/gallery/img/p.webp", "assets/gallery-webp/costume/o.webp", "assets/My Outfits/catalog.json",
                 "assets/cozy_poses/img/c.png", "assets/gallery-studio/x/img/d.png"}
    for relative in REQUIRED | bundled | elsewhere:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    assert set(shipped_paths(tmp_path, working_tree=True)) == REQUIRED | bundled


@pytest.mark.parametrize("relative, allowed", [
    ("assets/shipped/My Poses/catalog.json", True),
    ("assets/shipped/My Poses/img/a.webp", True),
    ("assets/shipped/My Poses/img/depth/a.webp", True),
    ("assets/shipped/My Poses/thumbs/a.webp", True),
    ("assets/shipped/My Poses/batch.json", False),      # raw Pose Studio run: holds local paths
    ("assets/shipped/My Poses/studio.json", False),
    ("assets/shipped/My Poses/img/a.png", False),       # unconverted drawing
    ("assets/shipped/My Poses/source/photo.webp", False),
    ("assets/shipped/notes.json", False),
    ("assets/My Poses/catalog.json", False),            # local galleries never ship
])
def test_only_bundled_gallery_files_pass_in_the_shipped_folder(relative, allowed):
    from scripts.check_release import shipped_gallery_file
    assert shipped_gallery_file(relative) is allowed
