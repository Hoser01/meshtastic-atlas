from atlas_muxdiag.updater import DEFAULT_MANIFEST_URL, version_key


def test_release_feed_is_https_and_version_order_is_numeric() -> None:
    assert DEFAULT_MANIFEST_URL.startswith("https://github.com/Hoser01/meshtastic-atlas/")
    assert version_key("0.3.10") > version_key("0.3.9")
    assert version_key("1.0.0-beta.1") == (1, 0, 0)
