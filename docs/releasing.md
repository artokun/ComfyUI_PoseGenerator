# Registry and GitHub release setup

## One-time registry setup

The package is configured for publisher **artokun**, matching
[comfyui-mcp-panel](https://github.com/artokun/comfyui-mcp-panel/blob/main/pyproject.toml).
The node ID is `comfyui-pose-generator`, initially version `0.1.0`.

1. Sign in to [Comfy Registry](https://registry.comfy.org/) with the account that
   owns or can publish as `artokun`. No second publisher account is needed.
2. In that publisher's settings, create a **Registry Publishing API Key**, or use
   an existing valid publishing key for the same publisher. This is separate from
   a Comfy inference/Partner Nodes key, a GitHub token, or the Tinify key.
3. In this GitHub repository, go to **Settings → Secrets and variables → Actions →
   New repository secret**. Save the key as **`REGISTRY_ACCESS_TOKEN`**. Secrets
   belonging only to the panel repository are not automatically shared here; an
   organization secret must explicitly grant this repository access.
4. Confirm the repository URL, publisher, and node ID in `pyproject.toml` before
   the first release. The registry node ID is immutable once created.

Do not paste a key into a PR, issue, README, or commit. A registry login is needed
to obtain/manage the key; GitHub Actions uses the saved key without an interactive
login during releases. No separate GitHub personal access token is needed by this
workflow.

Reference: [official publishing guide](https://docs.comfy.org/registry/publishing).

## Release a version

1. Publish the reviewed source and artwork to the public `main` branch at
   `artokun/ComfyUI_PoseGenerator`. The raw artwork URLs must be publicly reachable.
2. Add a changelog entry and increment `project.version` for each new release.
3. A change to `pyproject.toml` on `main` triggers **Publish to Comfy Registry**.
   You can also run it from **Actions → Publish to Comfy Registry → Run workflow**
   on `main`, including for the first release.
4. Shared checks must pass before upload: tests, frontend syntax, metadata,
   exact package contents, Python AST checks, YARA, and Bandit. The publish job
   then verifies every file checksum and uploads only that checked artifact.
5. Inspect the workflow and registry validation result before announcing availability.
   Each release needs a new version; do not retry a successfully published version
   as a different build.

The software package does not include gallery assets. Optional gallery distribution,
bulk setup, and screenshots containing those assets are pending permission from
the relevant rights holder; see [the source notices](../THIRD_PARTY.md).

## Artwork

| Destination | File | Format |
| --- | --- | --- |
| Registry icon | [`assets/icon.png`](../assets/icon.png) | 400 × 400 PNG |
| Registry banner / README | [`assets/banner.png`](../assets/banner.png) | 1792 × 768 PNG, 21:9 |
| GitHub social preview | [`assets/social-preview.jpg`](../assets/social-preview.jpg) | 1280 × 640 JPG, under 1 MB |

`pyproject.toml` already points to the icon and banner on the intended `main`
branch. Masters and generation prompts are retained in `assets/registry/`.
See [registry artwork specifications](https://docs.comfy.org/registry/specifications).

## GitHub social / Open Graph card

On the repository, open **Settings → General → Social preview → Edit → Upload an
image**, and choose `assets/social-preview.jpg`. This is the repository's Open
Graph preview image; committing an image or embedding it in the README alone
does not set the repository social card.

The social card reuses the registry artwork with the same colors, title, and
illustration. GitHub recommends 1280 × 640 and a file below 1 MB.
[Official instructions](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview).

This prepares the preview used where GitHub displays repository social images.
It does not control whether GitHub Mobile features the repository in Explore or
guarantee that every app surface uses the same preview. Allow for caching after
changing the image.
