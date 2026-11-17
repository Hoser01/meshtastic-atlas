"""Meshtastic native TCP stream framing."""

from __future__ import annotations

from dataclasses import dataclass

START1 = 0x94
START2 = 0xC3
ALT_START2 = 0x93
HEADER_SIZE = 4
DEFAULT_MAX_PAYLOAD = 512


@dataclass(frozen=True)
class Frame:
    payload: bytes
    raw: bytes


class FrameDecoder:
    """Decode fragmented/coalesced frames and resynchronize after corrupt input."""

    def __init__(self, max_payload: int = DEFAULT_MAX_PAYLOAD) -> None:
        if max_payload < 1 or max_payload > 65535:
            raise ValueError("max_payload must be between 1 and 65535")
        self.max_payload = max_payload
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        self.buffer.extend(data)
        frames: list[Frame] = []
        while True:
            start = self._find_start()
            if start < 0:
                keep = 1 if self.buffer and self.buffer[-1] == START1 else 0
                del self.buffer[: len(self.buffer) - keep]
                break
            if start:
                del self.buffer[:start]
            if len(self.buffer) < HEADER_SIZE:
                break
            size = int.from_bytes(self.buffer[2:4], "big")
            if size > self.max_payload:
                del self.buffer[0]
                continue
            total = HEADER_SIZE + size
            if len(self.buffer) < total:
                break
            raw = bytes(self.buffer[:total])
            del self.buffer[:total]
            frames.append(Frame(raw[HEADER_SIZE:], raw))
        return frames

    def _find_start(self) -> int:
        for index in range(max(0, len(self.buffer) - 1)):
            if self.buffer[index] == START1 and self.buffer[index + 1] in (START2, ALT_START2):
                return index
        return -1


def frame_payload(payload: bytes) -> bytes:
    if len(payload) > 65535:
        raise ValueError("payload is too large for Meshtastic framing")
    return bytes((START1, START2)) + len(payload).to_bytes(2, "big") + payload
