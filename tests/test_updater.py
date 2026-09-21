from atlas_muxdiag.updater import DEFAULT_MANIFEST_URL, relocate_virtualenv_scripts, version_key


def test_release_feed_is_https_and_version_order_is_numeric() -> None:
    assert DEFAULT_MANIFEST_URL.startswith("https://github.com/Hoser01/meshtastic-atlas/")
    assert version_key("0.3.10") > version_key("0.3.9")
    assert version_key("1.0.0-beta.1") == (1, 0, 0)


def test_relocate_virtualenv_scripts_rewrites_staging_prefix(tmp_path) -> None:
    staging = tmp_path / ".0.3.6.staging"
    destination = tmp_path / "0.3.6"
    scripts = staging / "bin"
    scripts.mkdir(parents=True)
    command = scripts / "atlas-observer"
    command.write_text(f"#!{staging}/bin/python3\nprint('ok')\n")

    relocate_virtualenv_scripts(staging, destination)

    assert command.read_text().startswith(f"#!{destination}/bin/python3\n")
