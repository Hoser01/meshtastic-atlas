import json
import sys

from atlas_server import observer_admin


def test_issue_list_and_revoke_observer_token(tmp_path, monkeypatch, capsys) -> None:
    path = tmp_path / "tokens.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "atlas-observer-token",
            "--file",
            str(path),
            "issue",
            "SITE1",
        ],
    )
    assert observer_admin.main() == 0
    token = capsys.readouterr().out.strip()
    assert len(token) >= 48
    assert json.loads(path.read_text())["SITE1"] == token
    assert path.stat().st_mode & 0o777 == 0o600

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "atlas-observer-token",
            "--file",
            str(path),
            "list",
        ],
    )
    assert observer_admin.main() == 0
    assert capsys.readouterr().out.strip() == "SITE1"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "atlas-observer-token",
            "--file",
            str(path),
            "revoke",
            "SITE1",
        ],
    )
    assert observer_admin.main() == 0
    assert json.loads(path.read_text()) == {}
