"""Manage file-backed, independently revocable observer ingestion tokens."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import tempfile
from pathlib import Path


def load(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(token, str) for key, token in value.items()
    ):
        raise SystemExit("token file must contain a string-to-string JSON object")
    return value


def save(path: Path, tokens: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".observer-tokens-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(tokens, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(prog="atlas-observer-token")
    parser.add_argument("--file", type=Path, default=Path("/etc/atlas/observer-tokens.json"))
    commands = parser.add_subparsers(dest="command", required=True)
    issue = commands.add_parser("issue")
    issue.add_argument("observer_id")
    revoke = commands.add_parser("revoke")
    revoke.add_argument("observer_id")
    commands.add_parser("list")
    args = parser.parse_args()
    tokens = load(args.file)
    if args.command == "issue":
        token = secrets.token_urlsafe(48)
        tokens[args.observer_id] = token
        save(args.file, tokens)
        print(token)
    elif args.command == "revoke":
        if tokens.pop(args.observer_id, None) is None:
            raise SystemExit(f"observer not found: {args.observer_id}")
        save(args.file, tokens)
    else:
        for observer_id in sorted(tokens):
            print(observer_id)
    return 0
