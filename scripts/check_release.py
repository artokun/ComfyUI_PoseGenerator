#!/usr/bin/env python3
"""Build and scan the same tracked-minus-comfyignore set used for publishing."""

import argparse
import ast
import collections
import hashlib
import json
import os
import re
import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

import pathspec
import yara

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {"__init__.py", "nodes.py", "library.py", "prompts.py", "routes.py",
            "web/pose-gallery.js", "web/pose-gallery.css", "pyproject.toml",
            "requirements.txt", "LICENSE", "THIRD_PARTY.md", "README.md",
            "docs/notices/anhao-LICENSE.txt"}
# Galleries made with Pose Studio from the publisher's own photos. Only their
# drawings, depth maps and catalogs ship; never the original gallery (assets/gallery*).
BUNDLED_GALLERIES = ("assets/Instagram Poses/", "assets/OF poses/")
BUNDLED_SUFFIXES = {".webp", ".json"}


def shipped_paths(root, working_tree=False):
    ignore = pathspec.GitIgnoreSpec.from_lines((root / ".comfyignore").read_text().splitlines())
    if working_tree:
        gitignore = pathspec.GitIgnoreSpec.from_lines((root / ".gitignore").read_text().splitlines())
        candidates = []
        for directory, dirs, filenames in os.walk(root, followlinks=False):
            relative = Path(directory).relative_to(root)
            dirs[:] = [name for name in dirs if name != ".git" and not
                       (ignore.match_file((relative / name).as_posix() + "/") or gitignore.match_file((relative / name).as_posix() + "/"))]
            candidates.extend((relative / name).as_posix() for name in filenames)
        candidates = [path for path in candidates if not gitignore.match_file(path)]
    else:
        candidates = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True, text=True).stdout.split("\0")
    return sorted(path for path in candidates if path and not ignore.match_file(path))


def validate_metadata(root):
    data = tomllib.loads((root / "pyproject.toml").read_text())
    project = data["project"]
    for name in ("name", "version", "description", "license"):
        if not project.get(name):
            raise ValueError(f"Missing project.{name}")
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", project["version"]):
        raise ValueError("Release version must be MAJOR.MINOR.PATCH.")
    if not data.get("tool", {}).get("comfy", {}).get("PublisherId"):
        raise ValueError("Missing registry PublisherId.")
    if f"## {project['version']}" not in (root / "CHANGELOG.md").read_text():
        raise ValueError("Current version needs a changelog entry.")
    return project


def validate_python(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines = collections.Counter(node.lineno for node in ast.walk(tree) if isinstance(node, ast.stmt))
    if any(count > 1 for count in lines.values()):
        raise ValueError(f"Multiple Python statements on one line: {path}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("exec", "eval"):
            raise ValueError(f"Dynamic evaluation in {path}:{node.lineno}")


def stage(root, working_tree=False):
    project = validate_metadata(root)
    paths = shipped_paths(root, working_tree)
    missing = REQUIRED.difference(paths)
    if missing:
        raise ValueError(f"Runtime files missing or excluded: {sorted(missing)}")
    target = root / ".build/package"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    rules = yara.compile(filepath=str(root / "security/registry.yar"))
    manifest = []
    findings = []
    for relative in paths:
        source = root / relative
        if not source.is_file() or source.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Missing or non-regular package file: {relative}")
        if source.name.startswith(".env") or relative.startswith(("assets/gallery", ".cache/", ".venv/")):
            raise ValueError(f"Private or third-party material would ship: {relative}")
        if relative not in REQUIRED and not (relative.startswith(BUNDLED_GALLERIES) and source.suffix in BUNDLED_SUFFIXES):
            raise ValueError(f"Unexpected file would ship: {relative}")
        content = source.read_bytes()
        if source.suffix == ".py":
            validate_python(source)
        matches = rules.match(data=content)
        findings.extend({"file": relative, "rule": match.rule} for match in matches)
        output = target / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
        manifest.append({"path": relative, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    report = {"name": project["name"], "version": project["version"], "files": manifest, "yara_findings": findings}
    (root / ".build/manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    if findings:
        raise ValueError(f"YARA preflight failed: {findings}")
    with zipfile.ZipFile(root / ".build/node.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for row in manifest:
            archive.write(target / row["path"], row["path"])
    print(f"Staged {len(paths)} files, {sum(row['size'] for row in manifest):,} bytes; zero known YARA matches.")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--working-tree", action="store_true", help="Check uncommitted files before the initial Git commit")
    args = parser.parse_args()
    stage(ROOT, args.working_tree)
