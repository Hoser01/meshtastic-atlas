"""Incremental parser for Meshtastic's TCP/serial stream framing."""

from __future__ import annotations

from dataclasses import dataclass

START1 = 0x94
START2 = 0xC3
ALT_START2 = 0x93
HEADER_SIZE = 4
DEFAULT_MAX_PAYLOAD = 512
NODELESS_WANT_CONFIG_ID = 69420


@dataclass(frozen=True)
class Frame:
    payload: bytes
    raw: bytes
    discarded_before: int = 0


class FrameDecoder:
    """Decode fragmented/coalesced frames and resynchronize after corrupt input."""

    def __init__(self, max_payload: int = DEFAULT_MAX_PAYLOAD) -> None:
        if max_payload < 1 or max_payload > 65535:
            raise ValueError("max_payload must be between 1 and 65535")
        self.max_payload = max_payload
        self.buffer = bytearray()
        self.discarded_bytes = 0
        self.invalid_headers = 0

    def feed(self, data: bytes) -> list[Frame]:
        self.buffer.extend(data)
        frames: list[Frame] = []
        discarded_for_next = 0

        while True:
            start = self._find_start()
            if start < 0:
                # Preserve a final 0x94 because it may be half of a split marker.
                keep = 1 if self.buffer and self.buffer[-1] == START1 else 0
                drop = len(self.buffer) - keep
                if drop:
                    del self.buffer[:drop]
                    self.discarded_bytes += drop
                    discarded_for_next += drop
                break

            if start:
                del self.buffer[:start]
                self.discarded_bytes += start
                discarded_for_next += start

            if len(self.buffer) < HEADER_SIZE:
                break

            size = int.from_bytes(self.buffer[2:4], "big")
            if size > self.max_payload:
                del self.buffer[0]
                self.invalid_headers += 1
                self.discarded_bytes += 1
                discarded_for_next += 1
                continue

            total = HEADER_SIZE + size
            if len(self.buffer) < total:
                break

            raw = bytes(self.buffer[:total])
            del self.buffer[:total]
            frames.append(Frame(raw[HEADER_SIZE:], raw, discarded_for_next))
            discarded_for_next = 0

        return frames

    def _find_start(self) -> int:
        for index in range(max(0, len(self.buffer) - 1)):
            if self.buffer[index] == START1 and self.buffer[index + 1] in (START2, ALT_START2):
                return index
        return -1


def frame_payload(payload: bytes) -> bytes:
    """Wrap a protobuf payload in Meshtastic's TCP/serial framing."""
    if len(payload) > 65535:
        raise ValueError("payload is too large for Meshtastic framing")
    return bytes((START1, START2)) + len(payload).to_bytes(2, "big") + payload


def stream_subscription_frame() -> bytes:
    """Request live events without replaying the radio's stored node database."""
    from meshtastic.protobuf import mesh_pb2

    request = mesh_pb2.ToRadio(want_config_id=NODELESS_WANT_CONFIG_ID)
    return frame_payload(request.SerializeToString())
