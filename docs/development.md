# Development

Use Python 3.11 for development tools and Node.js 22 for frontend validation.
The node itself supports Python 3.10 and later.

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
node --check web/pose-gallery.js
python scripts/check_release.py --working-tree
bandit -r .build/package -s B101,B112,B311
```

Once files are tracked, omit `--working-tree` to inspect tracked files filtered
through `.comfyignore`. `.build/node.zip` is the validated package;
`.build/manifest.json` records each file's SHA-256 checksum.

CI and publishing share [checks.yml](../.github/workflows/checks.yml): behavior
tests, frontend syntax, metadata and package-content checks, Python AST checks,
actual YARA scans, and Bandit. The Bandit exclusions match
[comfyui-mcp-panel](https://github.com/artokun/comfyui-mcp-panel).

The YARA rules cover the panel's known process-launch, image event-attribute,
and network-operation patterns. These are local preflight rules, not the registry's
complete private scanner or a guarantee of acceptance.

Publishing verifies the downloaded artifact against its manifest, reconstructs
only those files, and invokes the tested `comfy-cli==1.26.0` version. Development
tools, keys, screenshots, and gallery assets never enter the registry archive.

[Release setup](releasing.md) explains the one-time account and repository steps.
