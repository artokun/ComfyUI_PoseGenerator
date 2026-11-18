#!/usr/bin/env python3
"""Save the macOS clipboard as SAI_API_TOKEN in the project's .env."""

import os
from pathlib import Path
import re
import subprocess
import tempfile


def main():
    root = Path(__file__).resolve().parents[1]
    env_file = root / ".env"
    if env_file.is_symlink():
        raise SystemExit("Refusing to replace a symlinked .env.")
    if subprocess.check_output(["git", "ls-files", "--", ".env"], cwd=root).strip():
        raise SystemExit("Refusing to write a token to a tracked .env.")
    if subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=root).returncode:
        raise SystemExit("Add .env to .gitignore before running this script.")

    try:
        result = subprocess.run(["/usr/bin/pbpaste"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        raise SystemExit("Clipboard unavailable. Run this script in your Mac terminal.") from None
    token = result.stdout.decode("utf-8").strip()
    if not token or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in token):
        raise SystemExit("Copy only the token: one nonempty value without whitespace.")
    if "'" in token or "${" in token:
        raise SystemExit("Unexpected token characters; .env was not changed.")

    original = env_file.read_bytes().decode("utf-8") if env_file.exists() else ""
    newline = "\r\n" if "\r\n" in original else "\n"
    assignment = f"SAI_API_TOKEN='{token}'{newline}"
    pattern = re.compile(r"^\s*(?:export\s+)?(?:SAI_API_TOKEN|sai_api_token)\s*=")
    updated = []
    replaced = False
    for line in original.splitlines(keepends=True):
        if pattern.match(line):
            if not replaced:
                updated.append(assignment)
                replaced = True
        else:
            updated.append(line)
    if not replaced:
        if updated and not updated[-1].endswith(("\n", "\r")):
            updated[-1] += newline
        updated.append(assignment)

    fd, temporary = tempfile.mkstemp(prefix=".env-", dir=root)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write("".join(updated).encode("utf-8"))
        os.replace(temporary, env_file)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("Saved SAI_API_TOKEN to .env with owner-only permissions. Token not displayed.")


if __name__ == "__main__":
    main()
