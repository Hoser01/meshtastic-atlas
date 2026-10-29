"""Build cached terrain-based RF predictions for fixed ATLAS collectors."""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_BOUNDS = (-94.85, 35.82, -92.85, 37.80)
MODEL_NAME = "SPLAT Longley-Rice ITM"
MODEL_VERSION = "1.4.2-olditm"
TERRAIN_SOURCE = "USGS National Map 3DEP"
PREDICTION_COLORS = {
    "strong": (69, 224, 111, 108),
    "likely": (255, 122, 26, 92),
    "marginal": (165, 107, 255, 72),
}


def fetch_json(url: str, timeout: int = 60, attempts: int = 4) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "ATLAS-Coverage/0.3.40"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            if attempt + 1 == attempts:
                raise
            time.sleep(2**attempt)
    raise RuntimeError("unreachable retry state")


def load_sites(url: str) -> list[dict[str, Any]]:
    rows = fetch_json(url)
    if not isinstance(rows, list):
        raise TypeError("observer-health did not return an array")
    sites = []
    for row in rows:
        latitude, longitude = row.get("latitude"), row.get("longitude")
        if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
            continue
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            continue
        sites.append(
            {
                "observer_id": str(row["observer_id"]),
                "node_num": row.get("observer_node_num"),
                "long_name": row.get("long_name") or row.get("observer_id"),
                "latitude": float(latitude),
                "longitude": float(longitude),
            }
        )
    if not sites:
        raise RuntimeError("no positioned collector sites were returned")
    return sorted(sites, key=lambda row: row["observer_id"])


def haversine_km(first: tuple[float, float], second: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 6371.0088 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def select_secondary_anchors(
    payload: dict[str, Any],
    primary_sites: list[dict[str, Any]],
    bounds: tuple[float, float, float, float],
    maximum: int = 8,
    minimum_separation_km: float = 12.0,
) -> list[dict[str, Any]]:
    """Choose stable, well-supported, geographically distributed non-collector nodes."""
    west, south, east, north = bounds
    grouped: dict[int, list[dict[str, Any]]] = {}
    for feature in payload.get("features", []):
        geometry = feature.get("geometry", {})
        properties = feature.get("properties", {})
        coordinates = geometry.get("coordinates")
        node_num = properties.get("transmitter_node")
        if (
            geometry.get("type") != "Point"
            or not isinstance(coordinates, list)
            or len(coordinates) < 2
            or not isinstance(node_num, int)
        ):
            continue
        longitude, latitude = coordinates[:2]
        if not (south <= latitude <= north and west <= longitude <= east):
            continue
        grouped.setdefault(node_num, []).append(feature)

    primary_positions = [(site["latitude"], site["longitude"]) for site in primary_sites]
    primary_nodes = {site.get("node_num") for site in primary_sites}
    candidates: list[dict[str, Any]] = []
    for node_num, features in grouped.items():
        if node_num in primary_nodes or len(features) < 6:
            continue
        positions = [
            (feature["geometry"]["coordinates"][1], feature["geometry"]["coordinates"][0])
            for feature in features
        ]
        latitude = sorted(position[0] for position in positions)[len(positions) // 2]
        longitude = sorted(position[1] for position in positions)[len(positions) // 2]
        center = (latitude, longitude)
        position_spread_km = max(haversine_km(center, position) for position in positions)
        if position_spread_km > 2.0:
            continue
        observer_count = len({
            str(feature["properties"].get("observer_id")) for feature in features
        })
        day_count = len({
            int(float(feature["properties"].get("age_seconds") or 0) // 86400)
            for feature in features
        })
        direct_count = sum(int(feature["properties"].get("hops") or 0) == 0 for feature in features)
        if day_count < 2 or (observer_count < 2 and direct_count < 3):
            continue
        collector_distance_km = min(
            (haversine_km(center, position) for position in primary_positions), default=999.0
        )
        if collector_distance_km < 8.0:
            continue
        weighted_support = sum(
            max(0.0, min(1.0, float(feature["properties"].get("weight") or 0)))
            for feature in features
        )
        score = (
            weighted_support
            + day_count * 2
            + observer_count * 3
            + min(collector_distance_km, 50) / 5
        )
        candidates.append({
            "observer_id": f"NODE_{node_num:08X}",
            "node_num": node_num,
            "long_name": f"Evidence anchor !{node_num:08x}",
            "latitude": latitude,
            "longitude": longitude,
            "support_count": len(features),
            "direct_count": direct_count,
            "observer_count": observer_count,
            "day_count": day_count,
            "weighted_support": round(weighted_support, 3),
            "position_spread_km": round(position_spread_km, 3),
            "collector_distance_km": round(collector_distance_km, 3),
            "selection_score": round(score, 3),
        })

    selected: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: item["selection_score"], reverse=True):
        position = (candidate["latitude"], candidate["longitude"])
        if any(
            haversine_km(position, (anchor["latitude"], anchor["longitude"]))
            < minimum_separation_km
            for anchor in selected
        ):
            continue
        selected.append(candidate)
        if len(selected) >= maximum:
            break
    return selected


def terrain_tiles(sites: Iterable[dict[str, Any]], radius_km: float) -> set[tuple[int, int]]:
    """Return (south latitude, west longitude) one-degree tiles for each model radius."""
    result: set[tuple[int, int]] = set()
    for site in sites:
        latitude, longitude = site["latitude"], site["longitude"]
        lat_radius = radius_km / 111.32
        lon_radius = radius_km / (111.32 * max(0.2, math.cos(math.radians(latitude))))
        for south in range(math.floor(latitude - lat_radius), math.floor(latitude + lat_radius) + 1):
            for west in range(math.floor(longitude - lon_radius), math.floor(longitude + lon_radius) + 1):
                result.add((south, west))
    return result


def sdf_name(south: int, west: int) -> str:
    # SPLAT represents western longitudes as positive degrees west.
    return f"{south}:{south + 1}:{abs(west + 1)}:{abs(west)}.sdf"


def ensure_terrain_tile(south: int, west: int, terrain_dir: Path) -> Path:
    """Download one USGS 3DEP tile and convert it to standard SPLAT SDF."""
    expected = terrain_dir / sdf_name(south, west)
    if expected.exists() and expected.stat().st_size > 1_000_000:
        return expected
    terrain_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="atlas-terrain-", dir=terrain_dir) as temporary:
        temp = Path(temporary)
        query = urllib.parse.urlencode(
            {
                "bbox": f"{west},{south},{west + 1},{south + 1}",
                "bboxSR": "4326",
                "imageSR": "4326",
                "size": "1201,1201",
                "format": "tiff",
                "pixelType": "F32",
                "noData": "0",
                "interpolation": "RSP_BilinearInterpolation",
                "f": "json",
            }
        )
        metadata = fetch_json(
            "https://elevation.nationalmap.gov/arcgis/rest/services/"
            f"3DEPElevation/ImageServer/exportImage?{query}",
            timeout=180,
        )
        href = metadata.get("href") if isinstance(metadata, dict) else None
        if not href:
            raise RuntimeError(f"USGS did not return terrain for tile {south},{west}")
        tif_path = temp / "terrain.tif"
        request = urllib.request.Request(href, headers={"User-Agent": "ATLAS-Coverage/0.3.40"})
        with urllib.request.urlopen(request, timeout=300) as response, tif_path.open("wb") as output:
            shutil.copyfileobj(response, output)

        try:
            import numpy as np
            import rasterio
        except ImportError as exc:  # pragma: no cover - deployment validation handles this
            raise RuntimeError("coverage build requires numpy and rasterio") from exc
        with rasterio.open(tif_path) as dataset:
            values = dataset.read(1, out_shape=(1201, 1201), masked=True)
        if values.count() < values.size * 0.95:
            raise RuntimeError(f"terrain tile {south},{west} contains too many missing samples")
        fill = float(np.ma.median(values))
        elevation = np.ma.filled(values, fill_value=fill)
        elevation = np.clip(np.rint(elevation), -32768, 32767).astype(">i2")
        hgt_path = temp / f"N{south:02d}W{abs(west):03d}.hgt"
        elevation.tofile(hgt_path)
        subprocess.run(
            ["srtm2sdf", "-d", "/dev/null", str(hgt_path)],
            cwd=temp,
            check=True,
            timeout=120,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        generated = temp / expected.name
        if not generated.exists():
            raise RuntimeError(f"SPLAT conversion did not produce {expected.name}")
        os.replace(generated, expected)
    return expected


def parse_ano(lines: Iterable[str]) -> Iterable[tuple[float, float, float]]:
    """Yield latitude, longitude, and path loss from a SPLAT ANO stream."""
    for line in lines:
        if ";" in line or not line.strip():
            continue
        fields = line.replace("*", "").split(",")
        if len(fields) < 5:
            continue
        try:
            latitude = float(fields[0])
            longitude = -float(fields[1])
            path_loss = float(fields[4])
        except ValueError:
            continue
        if math.isfinite(latitude) and math.isfinite(longitude) and math.isfinite(path_loss):
            yield latitude, longitude, path_loss


def classify_path_loss(path_loss: float) -> str | None:
    if path_loss <= 131:
        return "strong"
    if path_loss <= 141:
        return "likely"
    if path_loss <= 151:
        return "marginal"
    return None


def aggregate_points(
    samples: Iterable[tuple[str, float, float, float]],
    bounds: tuple[float, float, float, float],
    cell_km: float,
) -> dict[tuple[int, int], dict[str, Any]]:
    west, south, east, north = bounds
    lat_step = cell_km / 111.32
    lon_step = cell_km / (111.32 * math.cos(math.radians((south + north) / 2)))
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    for observer_id, latitude, longitude, path_loss in samples:
        band = classify_path_loss(path_loss)
        if band is None or not (south <= latitude <= north and west <= longitude <= east):
            continue
        row = math.floor((latitude - south) / lat_step)
        column = math.floor((longitude - west) / lon_step)
        key = (row, column)
        existing = cells.get(key)
        if existing is None or path_loss < existing["path_loss_db"]:
            cells[key] = {
                "observer_id": observer_id,
                "path_loss_db": round(path_loss, 1),
                "band": band,
                "row": row,
                "column": column,
                "lat_step": lat_step,
                "lon_step": lon_step,
            }
    return cells


def blend_reachability_evidence(
    cells: dict[tuple[int, int], dict[str, Any]],
    payload: dict[str, Any],
    bounds: tuple[float, float, float, float],
    cell_km: float,
) -> dict[str, Any]:
    """Conservatively calibrate modeled cells with repeated positioned RF evidence."""
    if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
        raise TypeError("reachability endpoint did not return a FeatureCollection")
    west, south, east, north = bounds
    lat_step = cell_km / 111.32
    lon_step = cell_km / (111.32 * math.cos(math.radians((south + north) / 2)))
    evidence: dict[tuple[int, int], dict[str, Any]] = {}
    direct_samples = multihop_samples = 0
    for feature in payload["features"]:
        geometry = feature.get("geometry", {})
        properties = feature.get("properties", {})
        coordinates = geometry.get("coordinates")
        if geometry.get("type") != "Point" or not isinstance(coordinates, list) or len(coordinates) < 2:
            continue
        longitude, latitude = coordinates[:2]
        if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
            continue
        if not (south <= latitude <= north and west <= longitude <= east):
            continue
        hops = max(0, int(properties.get("hops") or 0))
        if hops > 7:
            continue
        weight = float(properties.get("weight") or 0)
        row = math.floor((latitude - south) / lat_step)
        column = math.floor((longitude - west) / lon_step)
        bucket = evidence.setdefault(
            (row, column),
            {
                "support_count": 0,
                "direct_count": 0,
                "multihop_count": 0,
                "weighted_support": 0.0,
                "observer_ids": set(),
                "node_ids": set(),
            },
        )
        bucket["support_count"] += 1
        bucket["weighted_support"] += max(0.0, min(1.0, weight))
        bucket["observer_ids"].add(str(properties.get("observer_id") or "UNKNOWN"))
        bucket["node_ids"].add(str(properties.get("transmitter_node") or "UNKNOWN"))
        if hops == 0:
            bucket["direct_count"] += 1
            direct_samples += 1
        else:
            bucket["multihop_count"] += 1
            multihop_samples += 1

    reinforced = extended = halo_cells = qualified = 0
    direct_halo_sources: list[tuple[tuple[int, int], dict[str, Any]]] = []
    reachability_halo_sources: list[tuple[tuple[int, int], dict[str, Any]]] = []
    rank = {"marginal": 0, "likely": 1, "strong": 2}
    bands = ("marginal", "likely", "strong")

    def evidence_properties(bucket: dict[str, Any], role: str) -> dict[str, Any]:
        return {
            "evidence_role": role,
            "evidence_support_count": bucket["support_count"],
            "direct_support_count": bucket["direct_count"],
            "multihop_support_count": bucket["multihop_count"],
            "weighted_support": round(bucket["weighted_support"], 3),
            "evidence_observer_count": len(bucket["observer_ids"]),
            "evidence_node_count": len(bucket["node_ids"]),
        }

    for key, bucket in evidence.items():
        direct_qualified = bucket["direct_count"] >= 2
        multihop_qualified = (
            bucket["multihop_count"] >= 3 and bucket["weighted_support"] >= 0.75
        )
        if not direct_qualified and not multihop_qualified:
            continue
        qualified += 1
        existing = cells.get(key)
        if existing is not None:
            original = existing["band"]
            should_reinforce = direct_qualified or (
                bucket["multihop_count"] >= 6 and len(bucket["observer_ids"]) >= 2
            )
            if should_reinforce and rank[original] < rank["strong"]:
                existing["band"] = bands[rank[original] + 1]
            existing.update(evidence_properties(bucket, "reinforced"))
            existing["terrain_band"] = original
            reinforced += 1
        else:
            band = "likely" if direct_qualified else "marginal"
            cells[key] = {
                "observer_id": "RF_EVIDENCE",
                "path_loss_db": 141.0 if band == "likely" else 151.0,
                "band": band,
                "terrain_band": None,
                "row": key[0],
                "column": key[1],
                "lat_step": lat_step,
                "lon_step": lon_step,
                **evidence_properties(bucket, "extended"),
            }
            extended += 1

        if bucket["direct_count"] >= 3:
            direct_halo_sources.append((key, bucket))
        elif (
            bucket["multihop_count"] >= 6
            and bucket["weighted_support"] >= 1.5
            and len(bucket["observer_ids"]) >= 2
        ):
            reachability_halo_sources.append((key, bucket))

    # Repeated direct reception supports a conservative one-cell local prediction.
    for key, bucket in direct_halo_sources:
        for row_offset, column_offset in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            neighbor = (key[0] + row_offset, key[1] + column_offset)
            latitude = south + neighbor[0] * lat_step
            longitude = west + neighbor[1] * lon_step
            if neighbor in cells or not (south <= latitude <= north and west <= longitude <= east):
                continue
            cells[neighbor] = {
                "observer_id": "RF_EVIDENCE",
                "path_loss_db": 151.0,
                "band": "marginal",
                "terrain_band": None,
                "row": neighbor[0],
                "column": neighbor[1],
                "lat_step": lat_step,
                "lon_step": lon_step,
                **evidence_properties(bucket, "direct_evidence_halo"),
            }
            halo_cells += 1

    reachability_halo_cells = 0
    for key, bucket in reachability_halo_sources:
        for row_offset, column_offset in (
            (-1, -1), (-1, 0), (-1, 1),
            (0, -1), (0, 1),
            (1, -1), (1, 0), (1, 1),
        ):
            neighbor = (key[0] + row_offset, key[1] + column_offset)
            latitude = south + neighbor[0] * lat_step
            longitude = west + neighbor[1] * lon_step
            if neighbor in cells or not (south <= latitude <= north and west <= longitude <= east):
                continue
            cells[neighbor] = {
                "observer_id": "RF_EVIDENCE",
                "path_loss_db": 151.0,
                "band": "marginal",
                "terrain_band": None,
                "row": neighbor[0],
                "column": neighbor[1],
                "lat_step": lat_step,
                "lon_step": lon_step,
                **evidence_properties(bucket, "reachability_evidence_halo"),
            }
            reachability_halo_cells += 1

    return {
        "status": "applied",
        "window_days": payload.get("metadata", {}).get("window_days"),
        "input_samples": direct_samples + multihop_samples,
        "direct_samples": direct_samples,
        "multihop_samples": multihop_samples,
        "evidence_cells": len(evidence),
        "qualified_cells": qualified,
        "reinforced_cells": reinforced,
        "extended_cells": extended,
        "direct_halo_cells": halo_cells,
        "reachability_halo_cells": reachability_halo_cells,
        "rules": {
            "direct_minimum": 2,
            "multihop_minimum": 3,
            "multihop_weighted_support_minimum": 0.75,
            "multihop_halo_minimum": 6,
            "multihop_halo_weighted_support_minimum": 1.5,
            "activity_creates_coverage": False,
        },
    }


def cells_to_geojson(
    cells: dict[tuple[int, int], dict[str, Any]],
    bounds: tuple[float, float, float, float],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    west, south, east, north = bounds
    colors = {"strong": "#45e06f", "likely": "#ff7a1a", "marginal": "#a56bff"}
    features = []
    for cell in sorted(cells.values(), key=lambda item: (item["row"], item["column"])):
        cell_south = south + cell["row"] * cell["lat_step"]
        cell_north = min(north, cell_south + cell["lat_step"])
        cell_west = west + cell["column"] * cell["lon_step"]
        cell_east = min(east, cell_west + cell["lon_step"])
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "observer_id": cell["observer_id"],
                    "path_loss_db": cell["path_loss_db"],
                    "link_margin_db": round(151 - cell["path_loss_db"], 1),
                    "band": cell["band"],
                    "color": colors[cell["band"]],
                    "confidence": "modeled",
                    "terrain_band": cell.get("terrain_band", cell["band"]),
                    "evidence_role": cell.get("evidence_role"),
                    "evidence_support_count": cell.get("evidence_support_count", 0),
                    "direct_support_count": cell.get("direct_support_count", 0),
                    "multihop_support_count": cell.get("multihop_support_count", 0),
                    "weighted_support": cell.get("weighted_support", 0),
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [cell_west, cell_south], [cell_east, cell_south],
                        [cell_east, cell_north], [cell_west, cell_north],
                        [cell_west, cell_south],
                    ]],
                },
            }
        )
    return {"type": "FeatureCollection", "features": features, "metadata": metadata}


def cells_to_png(
    cells: dict[tuple[int, int], dict[str, Any]],
    bounds: tuple[float, float, float, float],
) -> tuple[bytes, int, int]:
    """Render the modeled grid once so browsers do not construct thousands of polygons."""
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - deployment validation handles this
        raise RuntimeError("coverage build requires Pillow") from exc
    if not cells:
        raise RuntimeError("coverage model produced no publishable cells")
    west, south, east, north = bounds
    sample = next(iter(cells.values()))
    width = max(1, math.ceil((east - west) / sample["lon_step"]))
    height = max(1, math.ceil((north - south) / sample["lat_step"]))
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = image.load()
    for cell in cells.values():
        x = cell["column"]
        y = height - 1 - cell["row"]
        if 0 <= x < width and 0 <= y < height:
            pixels[x, y] = PREDICTION_COLORS[cell["band"]]
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue(), width, height


def run_site(
    site: dict[str, Any],
    terrain_dir: Path,
    work_dir: Path,
    radius_km: float,
    bounds: tuple[float, float, float, float],
    cell_km: float,
    antenna_height_m: float = 3.0,
    path_loss_penalty_db: float = 0.0,
    site_type: str = "collector",
) -> tuple[dict[tuple[int, int], dict[str, Any]], dict[str, Any]]:
    identifier = site["observer_id"]
    safe_identifier = "".join(character for character in identifier if character.isalnum() or character in "_-")
    qth = work_dir / f"{safe_identifier}.qth"
    qth.write_text(
        f"{identifier}\n{site['latitude']}\n{abs(site['longitude'])}\n{antenna_height_m}m\n",
        encoding="utf-8",
    )
    qth.with_suffix(".lrp").write_text(
        "15.000\n0.005\n301.000\n915.000\n5\n1\n0.90\n0.90\n",
        encoding="utf-8",
    )
    ano = work_dir / f"{safe_identifier}.ano"
    command = [
        "splat", "-t", str(qth), "-L", "1.5", "-metric", "-R", str(radius_km),
        "-d", str(terrain_dir), "-db", "153", "-ano", str(ano), "-N", "-olditm",
    ]
    completed = subprocess.run(
        command,
        cwd=work_dir,
        check=True,
        timeout=900,
        capture_output=True,
        text=True,
    )
    if not ano.exists():
        raise RuntimeError(f"SPLAT did not produce an ANO file for {identifier}")
    raw_sample_count = 0

    def samples() -> Iterable[tuple[str, float, float, float]]:
        nonlocal raw_sample_count
        with ano.open(encoding="utf-8") as stream:
            for latitude, longitude, path_loss in parse_ano(stream):
                raw_sample_count += 1
                yield identifier, latitude, longitude, path_loss + path_loss_penalty_db

    cells = aggregate_points(samples(), bounds, cell_km)
    return cells, {
        "observer_id": identifier,
        "long_name": site["long_name"],
        "latitude": site["latitude"],
        "longitude": site["longitude"],
        "raw_sample_count": raw_sample_count,
        "published_cell_count": len(cells),
        "site_type": site_type,
        "antenna_height_m": antenna_height_m,
        "path_loss_penalty_db": path_loss_penalty_db,
        "model_log_tail": completed.stdout.strip().splitlines()[-1:] or [],
    }


def publish_build(
    output_root: Path,
    payload: dict[str, Any],
    metadata: dict[str, Any],
    raster: bytes,
    keep: int = 3,
) -> Path:
    builds = output_root / "builds"
    builds.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    build = builds / stamp
    build.mkdir()
    (build / "predicted.geojson").write_text(
        json.dumps(payload, separators=(",", ":")), encoding="utf-8"
    )
    (build / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8"
    )
    (build / "predicted.png").write_bytes(raster)
    # Validate both files before atomically publishing the new build.
    json.loads((build / "predicted.geojson").read_text(encoding="utf-8"))
    json.loads((build / "metadata.json").read_text(encoding="utf-8"))
    if not (build / "predicted.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("invalid prediction raster")
    temporary_link = output_root / ".current-new"
    temporary_link.unlink(missing_ok=True)
    temporary_link.symlink_to(build.relative_to(output_root), target_is_directory=True)
    os.replace(temporary_link, output_root / "current")
    old_builds = sorted((path for path in builds.iterdir() if path.is_dir()), reverse=True)
    for old in old_builds[keep:]:
        shutil.rmtree(old)
    return build


def build(args: argparse.Namespace) -> Path:
    output_root = Path(args.output).resolve()
    terrain_dir = output_root / "terrain"
    sites = load_sites(args.observers_url)
    reachability = fetch_json(args.reachability_url, timeout=180)
    secondary_anchors = select_secondary_anchors(
        reachability,
        sites,
        args.bounds,
        maximum=args.secondary_anchor_limit,
        minimum_separation_km=args.secondary_minimum_separation_km,
    )
    required_tiles = terrain_tiles(sites, args.radius_km) | terrain_tiles(
        secondary_anchors, args.secondary_radius_km
    )
    for south, west in sorted(required_tiles):
        ensure_terrain_tile(south, west, terrain_dir)
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    site_results = []
    secondary_results = []
    with tempfile.TemporaryDirectory(prefix="atlas-coverage-") as temporary:
        for site in sites:
            work = Path(temporary) / site["observer_id"]
            work.mkdir()
            site_cells, result = run_site(
                site, terrain_dir, work, args.radius_km, args.bounds, args.cell_km
            )
            for key, candidate in site_cells.items():
                existing = cells.get(key)
                if existing is None or candidate["path_loss_db"] < existing["path_loss_db"]:
                    cells[key] = candidate
            site_results.append(result)
        for anchor in secondary_anchors:
            work = Path(temporary) / anchor["observer_id"]
            work.mkdir()
            anchor_cells, result = run_site(
                anchor,
                terrain_dir,
                work,
                args.secondary_radius_km,
                args.bounds,
                args.cell_km,
                antenna_height_m=1.5,
                path_loss_penalty_db=4.0,
                site_type="evidence_anchor",
            )
            for key, candidate in anchor_cells.items():
                existing = cells.get(key)
                if existing is None or candidate["path_loss_db"] < existing["path_loss_db"]:
                    cells[key] = candidate
            secondary_results.append({**anchor, **result})
    evidence_calibration = blend_reachability_evidence(
        cells, reachability, args.bounds, args.cell_km
    )
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    band_counts = {
        band: sum(cell["band"] == band for cell in cells.values())
        for band in ("strong", "likely", "marginal")
    }
    metadata = {
        "status": "ok",
        "generated_at": generated_at,
        "model": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "terrain_source": TERRAIN_SOURCE,
        "terrain_resolution_arc_seconds": 3,
        "frequency_mhz": 915,
        "modem_preset": "LongFast",
        "collector_antenna_height_m": 3.0,
        "client_antenna_height_m": 1.5,
        "tx_power_dbm": 22,
        "antenna_gain_dbi": 0,
        "implementation_loss_db": 2,
        "theoretical_link_budget_db": 153,
        "practical_path_loss_limit_db": 151,
        "reliability": 0.90,
        "confidence": 0.90,
        "cell_km": args.cell_km,
        "radius_km": args.radius_km,
        "secondary_anchor_radius_km": args.secondary_radius_km,
        "bounds": list(args.bounds),
        "collector_count": len(sites),
        "secondary_anchor_count": len(secondary_anchors),
        "cell_count": len(cells),
        "band_counts": band_counts,
        "evidence_calibration": evidence_calibration,
        "collectors": site_results,
        "secondary_anchors": secondary_results,
        "disclaimer": (
            "Terrain-based direct-link estimate from collectors and conservatively modeled "
            "stable RF-active mesh anchors, calibrated with repeated positioned RF reception "
            "and weighted multihop reachability evidence. Activity alone never creates "
            "coverage. Buildings, foliage, interference, antenna orientation, and installation "
            "quality are not modeled."
        ),
    }
    raster, raster_width, raster_height = cells_to_png(cells, args.bounds)
    metadata["raster_width"] = raster_width
    metadata["raster_height"] = raster_height
    payload = cells_to_geojson(cells, args.bounds, metadata)
    return publish_build(output_root, payload, metadata, raster)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="atlas-coverage-builder")
    parser.add_argument("--output", default="/var/lib/atlas/coverage")
    parser.add_argument(
        "--observers-url", default="http://127.0.0.1:18081/api/v1/observer-health"
    )
    parser.add_argument(
        "--reachability-url",
        default=(
            "http://127.0.0.1:18081/api/v1/coverage/"
            "reachability-surface?days=30&limit=350000"
        ),
    )
    parser.add_argument("--radius-km", type=float, default=80.0)
    parser.add_argument("--secondary-radius-km", type=float, default=30.0)
    parser.add_argument("--secondary-anchor-limit", type=int, default=20)
    parser.add_argument("--secondary-minimum-separation-km", type=float, default=5.0)
    parser.add_argument("--cell-km", type=float, default=1.0)
    parser.add_argument("--bounds", type=float, nargs=4, default=DEFAULT_BOUNDS,
                        metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    build_path = build(args)
    print(build_path)


if __name__ == "__main__":
    main()
