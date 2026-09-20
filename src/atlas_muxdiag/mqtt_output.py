"""Optional MQTT transport for normalized events; disabled unless configured."""

from __future__ import annotations

import json
import threading
from typing import Any


def event_topic(prefix: str, event: dict[str, Any]) -> str:
    groups = {
        "rf_observation": "observation",
        "local_transmission": "transmission",
        "local_transmission_queued": "transmission",
        "local_transmission_response": "transmission",
        "network_packet": "network",
        "unclassified_packet": "unknown",
    }
    group = groups.get(event["event"], "status")
    return f"{prefix.strip('/')}/{group}/{event['observer_id']}"


class MqttPublisher:
    def __init__(
        self,
        host: str,
        port: int,
        prefix: str,
        client_id: str,
        username: str | None = None,
        password: str | None = None,
        tls: bool = False,
    ) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise RuntimeError("install ATLAS with the [mqtt] extra to enable MQTT") from exc
        self.prefix = prefix
        self.connected = threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self.client.on_connect = self._on_connect
        if username:
            self.client.username_pw_set(username, password)
        if tls:
            self.client.tls_set()
        self.client.connect(host, port, keepalive=60)
        self.client.loop_start()
        if not self.connected.wait(10):
            self.close()
            raise TimeoutError(f"MQTT connection to {host}:{port} timed out")

    def _on_connect(
        self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any
    ) -> None:
        if int(reason_code) == 0:
            self.connected.set()

    def write(self, event: dict[str, Any]) -> None:
        payload = json.dumps(event, separators=(",", ":"), sort_keys=True)
        info = self.client.publish(event_topic(self.prefix, event), payload, qos=1, retain=False)
        info.wait_for_publish(timeout=5)
        if info.rc != 0:
            raise ConnectionError(f"MQTT publish failed with result code {info.rc}")

    def close(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()
