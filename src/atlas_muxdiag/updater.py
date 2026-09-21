"""Atomic, checksum-verified remote observer release updater."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import venv
from pathlib import Path
from urllib.parse import urlparse

from . import __version__

VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?$")
DEFAULT_MANIFEST_URL = (
    "https://github.com/Hoser01/meshtastic-atlas/releases/latest/download/observer-manifest.json"
)


def version_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.split(r"[-+]", value, maxsplit=1)[0].split("."))


def wheel_name(artifact_url: str) -> str:
    name = Path(urlparse(artifact_url).path).name
    if not name.endswith(".whl"):
        raise ValueError("update artifact must be a wheel")
    return name


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, timeout=300)


def relocate_virtualenv_scripts(staging: Path, destination: Path) -> None:
    """Rewrite absolute virtualenv paths before atomically moving a release."""
    old = os.fsencode(staging)
    new = os.fsencode(destination)
    for script in (staging / "bin").iterdir():
        if not script.is_file() or script.is_symlink():
            continue
        content = script.read_bytes()
        if old in content:
            script.write_bytes(content.replace(old, new))


def main() -> int:
    manifest_url = os.environ.get("ATLAS_UPDATE_MANIFEST_URL", DEFAULT_MANIFEST_URL).strip()
    if manifest_url.lower() == "disabled":
        print("ATLAS observer automatic updates are disabled")
        return 0
    if not manifest_url.startswith("https://"):
        raise SystemExit("update manifest must use HTTPS")
    root = Path(os.environ.get("ATLAS_RELEASE_ROOT", "/opt/atlas-observer")).resolve()
    service = os.environ.get("ATLAS_OBSERVER_SERVICE", "atlas-observer.service")
    keep = max(2, int(os.environ.get("ATLAS_RELEASES_TO_KEEP", "2")))
    with urllib.request.urlopen(manifest_url, timeout=15) as response:
        manifest = json.load(response)
    version = str(manifest.get("version", ""))
    artifact_url = str(manifest.get("url", ""))
    expected = str(manifest.get("sha256", "")).lower()
    if not VERSION.fullmatch(version) or not artifact_url.startswith("https://"):
        raise SystemExit("invalid update manifest")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise SystemExit("update manifest is missing a valid SHA-256 digest")
    try:
        artifact_name = wheel_name(artifact_url)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if version_key(version) <= version_key(__version__):
        print(f"ATLAS observer {__version__} is current; feed offers {version}")
        return 0
    releases = root / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    destination = releases / version
    if destination.exists():
        raise SystemExit(f"release already exists but is not active: {destination}")
    with tempfile.TemporaryDirectory(prefix="atlas-update-") as temporary:
        artifact = Path(temporary) / artifact_name
        digest = hashlib.sha256()
        with (
            urllib.request.urlopen(artifact_url, timeout=30) as response,
            artifact.open("wb") as stream,
        ):
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                stream.write(chunk)
        if digest.hexdigest() != expected:
            raise SystemExit("downloaded observer package failed SHA-256 verification")
        staging = releases / f".{version}.staging"
        shutil.rmtree(staging, ignore_errors=True)
        venv.EnvBuilder(with_pip=True, clear=True).create(staging)
        run([str(staging / "bin/pip"), "install", "--no-input", str(artifact)])
        run([str(staging / "bin/atlas-observer"), "--check-config"])
        relocate_virtualenv_scripts(staging, destination)
        os.replace(staging, destination)
    current = root / "current"
    previous_target = current.resolve() if current.is_symlink() else None
    replacement = root / ".current.new"
    replacement.unlink(missing_ok=True)
    replacement.symlink_to(destination)
    os.replace(replacement, current)
    try:
        run(["systemctl", "restart", service])
        run(["systemctl", "is-active", "--quiet", service])
    except Exception:
        if previous_target is not None:
            rollback = root / ".current.rollback"
            rollback.unlink(missing_ok=True)
            rollback.symlink_to(previous_target)
            os.replace(rollback, current)
            subprocess.run(["systemctl", "restart", service], check=False)
        raise
    installed = sorted(
        (path for path in releases.iterdir() if path.is_dir() and not path.name.startswith(".")),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for old in installed[keep:]:
        if old.resolve() != current.resolve():
            shutil.rmtree(old)
    print(f"updated ATLAS observer from {__version__} to {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
