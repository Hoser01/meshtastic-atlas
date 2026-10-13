from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import requests
from flask import Flask, jsonify, redirect, request


APP_DIR = Path(__file__).resolve().parent
ATLAS_FEED_URL = os.getenv(
    "ATLAS_FEED_URL", "http://127.0.0.1:18081/api/v1/map-feed"
)
ATLAS_TIMEOUT_SECONDS = float(os.getenv("ATLAS_TIMEOUT_SECONDS", "10"))
ATLAS_CACHE_SECONDS = float(os.getenv("ATLAS_CACHE_SECONDS", "20"))

app = Flask(__name__, static_folder=str(APP_DIR / "static"), static_url_path="/static")
_cache_lock = threading.Lock()
_cache: tuple[float, dict] = (0.0, {})


def fetch_atlas_nodes() -> dict:
    """Fetch a short-lived ATLAS snapshot, falling back to the last good one."""
    global _cache
    now = time.monotonic()
    with _cache_lock:
        cached_at, cached = _cache
        if cached and now - cached_at < ATLAS_CACHE_SECONDS:
            return cached
        try:
            response = requests.get(ATLAS_FEED_URL, timeout=ATLAS_TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("ATLAS map feed must be a JSON object")
            _cache = (now, payload)
            return payload
        except Exception:
            if cached:
                app.logger.warning("Serving stale ATLAS snapshot after refresh failure")
                return cached
            raise


@app.get("/")
def index():
    page = (APP_DIR / "new.html").read_text(encoding="utf-8")
    carto_key = os.getenv("NEXT_PUBLIC_CARTO_API_KEY", "").strip()
    page = page.replace("__CARTO_API_KEY__", json.dumps(carto_key))
    return page, 200, {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-cache",
    }


@app.get("/health")
def health():
    try:
        nodes = fetch_atlas_nodes()
        return jsonify({"status": "ok", "source": "atlas", "nodes": len(nodes)})
    except Exception as error:
        return jsonify({"status": "error", "source": "atlas", "error": str(error)}), 503


@app.get("/new")
@app.get("/new/")
def new_map_redirect():
    return redirect("/", code=302)


@app.get("/new/nodes")
def new_map_nodes():
    try:
        periods = {"live": 30, "day": 1440, "three_day": 4320, "ninety": 129_600}
        period = request.args.get("period", "live")
        if period not in periods:
            return jsonify({"error": "period must be live, day, three_day, or ninety"}), 400
        payload = {}
        for key, node in fetch_atlas_nodes().items():
            if not isinstance(node, dict):
                continue
            age = int(node.get("ageMin") if node.get("ageMin") is not None else 999_999)
            if age > periods[period]:
                continue
            payload[key] = {
                field: node.get(field)
                for field in (
                    "id", "node_num", "short_name", "name", "hardware_model", "role",
                    "lat", "lng", "ageMin", "position_source", "src_rf", "src_mqtt",
                    "last_source", "observer_id",
                )
            }
            telemetry = node.get("telemetry")
            payload[key]["telemetry"] = (
                {
                    field: telemetry.get(field)
                    for field in (
                        "ts", "battery", "voltage", "ch_util", "temp_c", "rh",
                        "pressure_hpa", "gas_kohm",
                    )
                }
                if isinstance(telemetry, dict)
                else None
            )
        response = jsonify(payload)
        response.headers["Cache-Control"] = "public, max-age=20, stale-while-revalidate=60"
        return response
    except Exception as error:
        app.logger.exception("ATLAS-only map feed unavailable")
        return jsonify({"error": str(error)}), 502


@app.get("/new/summary")
def new_map_summary():
    try:
        nodes = [node for node in fetch_atlas_nodes().values() if isinstance(node, dict)]
        ages = [
            int(node.get("ageMin") if node.get("ageMin") is not None else 999_999)
            for node in nodes
        ]
        response = jsonify(
            {
                "live": sum(age <= 30 for age in ages),
                "day": sum(age <= 1440 for age in ages),
                "ninety": sum(age <= 129_600 for age in ages),
                "all": len(nodes),
            }
        )
        response.headers["Cache-Control"] = "public, max-age=20, stale-while-revalidate=60"
        return response
    except Exception as error:
        app.logger.exception("ATLAS map summary unavailable")
        return jsonify({"error": str(error)}), 502


@app.get("/new/observers")
def new_map_observers():
    try:
        response = requests.get(
            ATLAS_FEED_URL.rsplit("/", 1)[0] + "/observers",
            timeout=ATLAS_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("ATLAS observers response must be a JSON array")
        result = jsonify(payload)
        result.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=900"
        return result
    except Exception as error:
        app.logger.exception("ATLAS observer feed unavailable")
        return jsonify({"error": str(error)}), 502
