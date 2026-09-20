#!/usr/bin/env python3
"""Create local MQTT diagnostic configuration from the authoritative LZ page."""

from __future__ import annotations

import argparse
import grp
import json
import os
import shlex
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

SOURCE_URL = "https://lzmesh.com/resources/lz-quick-setup/"


class TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.parts.append(value)


def following(parts: list[str], section: str, label: str) -> str:
    start = parts.index(section)
    position = parts.index(label, start)
    return parts[position + 1]


def atomic_write(path: Path, contents: str, group: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    group_id = grp.getgrnam(group).gr_gid
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as stream:
        stream.write(contents)
        temporary = Path(stream.name)
    os.chmod(temporary, 0o640)
    os.chown(temporary, 0, group_id)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("/etc/atlas"))
    parser.add_argument("--group", default="codex")
    args = parser.parse_args()

    request = Request(SOURCE_URL, headers={"User-Agent": "ATLAS-MQTT-Diagnostic/0.2"})
    collector = TextCollector()
    with urlopen(request, timeout=20) as response:
        collector.feed(response.read().decode("utf-8"))
    parts = collector.parts

    keys = {
        "0": {"name": "Default Meshtastic", "psk": following(parts, "Channel 0", "PSK")},
        "1": {"name": "LZMesh", "psk": following(parts, "Channel 1", "PSK")},
        "3": {"name": "LZRF", "psk": following(parts, "Channel 3", "PSK")},
    }
    username = following(parts, "MQTT quick setup.", "Username")
    password = following(parts, "MQTT quick setup.", "Password")
    environment = "\n".join(
        (
            f"ATLAS_MQTT_USERNAME={shlex.quote(username)}",
            f"ATLAS_MQTT_PASSWORD={shlex.quote(password)}",
            "",
        )
    )

    atomic_write(args.directory / "mqttdiag.channels.json", json.dumps(keys, indent=2) + "\n", args.group)
    atomic_write(args.directory / "mqttdiag.env", environment, args.group)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
