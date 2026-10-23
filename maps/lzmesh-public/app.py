from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import requests
from flask import Flask, jsonify, redirect, request

from snapshot_cache import SnapshotCache


APP_DIR = Path(__file__).resolve().parent
ATLAS_FEED_URL = os.getenv(
    "ATLAS_FEED_URL", "http://127.0.0.1:18081/api/v1/map-feed"
)
ATLAS_TIMEOUT_SECONDS = max(0.1, float(os.getenv("ATLAS_TIMEOUT_SECONDS", "2.5")))
ATLAS_CACHE_SECONDS = float(os.getenv("ATLAS_CACHE_SECONDS", "20"))

app = Flask(__name__, static_folder=str(APP_DIR / "static"), static_url_path="/static")
app.logger.setLevel(logging.INFO)
ATLAS_RETRY_SECONDS = float(os.getenv("ATLAS_RETRY_SECONDS", "5"))
ATLAS_RETRY_MAX_SECONDS = float(os.getenv("ATLAS_RETRY_MAX_SECONDS", "60"))
ATLAS_CACHE_DIRECTORY = os.getenv("ATLAS_CACHE_DIRECTORY")


def _read_feed(url: str, expected_type: type):
    response = requests.get(url, timeout=ATLAS_TIMEOUT_SECONDS)
    try:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, expected_type):
            raise ValueError("Unexpected ATLAS response schema")
        return payload
    finally:
        response.close()


_nodes_cache = SnapshotCache(
    "nodes", lambda: _read_feed(ATLAS_FEED_URL, dict), dict,
    ttl=ATLAS_CACHE_SECONDS, retry_base=ATLAS_RETRY_SECONDS,
    retry_max=ATLAS_RETRY_MAX_SECONDS, directory=ATLAS_CACHE_DIRECTORY,
    logger=app.logger,
)
_observers_cache = SnapshotCache(
    "observers", lambda: _read_feed(ATLAS_FEED_URL.rsplit("/", 1)[0] + "/observers", list), list,
    ttl=300, retry_base=ATLAS_RETRY_SECONDS,
    retry_max=ATLAS_RETRY_MAX_SECONDS, directory=ATLAS_CACHE_DIRECTORY,
    logger=app.logger,
)


def fetch_atlas_nodes() -> dict:
    return _nodes_cache.get()


def _cache_headers(response, cache):
    status = cache.status()
    response.headers["X-Atlas-Cache"] = (
        "empty" if not status["ready"] else "stale" if status["stale"] else "fresh"
    )
    if status["age_seconds"] is not None:
        response.headers["X-Atlas-Cache-Age"] = str(status["age_seconds"])
    if not status["ready"]:
        response.headers["Cache-Control"] = "no-store"
    return response


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
    nodes = fetch_atlas_nodes()
    status = _nodes_cache.status()
    response = jsonify({
        "status": "ok" if status["ready"] else "warming",
        "source": "atlas", "nodes": len(nodes), "cache": status,
    })
    response.headers["Cache-Control"] = "no-store"
    return response, 200 if status["ready"] else 503


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
        return _cache_headers(response, _nodes_cache)
    except Exception as error:
        app.logger.error(json.dumps({"event": "atlas_render_failure", "feed": "nodes", "error_type": type(error).__name__}))
        return jsonify({"error": "ATLAS map feed unavailable"}), 502


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
        return _cache_headers(response, _nodes_cache)
    except Exception as error:
        app.logger.error(json.dumps({"event": "atlas_render_failure", "feed": "summary", "error_type": type(error).__name__}))
        return jsonify({"error": "ATLAS map feed unavailable"}), 502


@app.get("/new/observers")
def new_map_observers():
    response = jsonify(_observers_cache.get())
    response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=900"
    return _cache_headers(response, _observers_cache)
