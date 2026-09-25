from meshtastic.protobuf import mesh_pb2

from atlas_muxdiag.framing import (
    ALT_START2,
    NODELESS_WANT_CONFIG_ID,
    START1,
    START2,
    FrameDecoder,
    frame_payload,
    stream_subscription_frame,
)


def framed(payload: bytes, second: int = START2) -> bytes:
    return bytes((START1, second)) + len(payload).to_bytes(2, "big") + payload


def test_fragmented_frame() -> None:
    decoder = FrameDecoder()
    wire = framed(b"abc")
    assert decoder.feed(wire[:1]) == []
    assert decoder.feed(wire[1:4]) == []
    frames = decoder.feed(wire[4:])
    assert [frame.payload for frame in frames] == [b"abc"]


def test_multiple_frames_and_alternate_marker() -> None:
    frames = FrameDecoder().feed(framed(b"a") + framed(b"bc", ALT_START2))
    assert [frame.payload for frame in frames] == [b"a", b"bc"]


def test_noise_and_invalid_length_resynchronize() -> None:
    decoder = FrameDecoder(max_payload=10)
    invalid = bytes((START1, START2, 0, 20))
    frames = decoder.feed(b"noise" + invalid + framed(b"ok"))
    assert [frame.payload for frame in frames] == [b"ok"]
    assert decoder.invalid_headers == 1
    assert decoder.discarded_bytes >= 9


def test_split_start_marker_is_preserved() -> None:
    decoder = FrameDecoder()
    assert decoder.feed(b"noise" + bytes((START1,))) == []
    frames = decoder.feed(bytes((START2, 0, 1)) + b"x")
    assert [frame.payload for frame in frames] == [b"x"]


def test_frame_payload_rejects_oversized_payload() -> None:
    try:
        frame_payload(b"x" * 65536)
    except ValueError as exc:
        assert "too large" in str(exc)
    else:
        raise AssertionError("oversized payload was accepted")


def test_stream_subscription_is_nodeless_and_non_transmitting() -> None:
    frames = FrameDecoder().feed(stream_subscription_frame())
    assert len(frames) == 1
    request = mesh_pb2.ToRadio.FromString(frames[0].payload)
    assert request.WhichOneof("payload_variant") == "want_config_id"
    assert request.want_config_id == NODELESS_WANT_CONFIG_ID
    assert not request.HasField("packet")
