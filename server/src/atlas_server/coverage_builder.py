"""Build cached terrain-based RF predictions for fixed ATLAS collectors."""

from __future__ import annotations

import argparse
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


def fetch_json(url: str, timeout: int = 60, attempts: int = 4) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "ATLAS-Coverage/0.3.35"})
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
                "long_name": row.get("long_name") or row.get("observer_id"),
                "latitude": float(latitude),
                "longitude": float(longitude),
            }
        )
    if not sites:
        raise RuntimeError("no positioned collector sites were returned")
    return sorted(sites, key=lambda row: row["observer_id"])


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
        request = urllib.request.Request(href, headers={"User-Agent": "ATLAS-Coverage/0.3.35"})
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


def run_site(
    site: dict[str, Any],
    terrain_dir: Path,
    work_dir: Path,
    radius_km: float,
    bounds: tuple[float, float, float, float],
    cell_km: float,
) -> tuple[dict[tuple[int, int], dict[str, Any]], dict[str, Any]]:
    identifier = site["observer_id"]
    safe_identifier = "".join(character for character in identifier if character.isalnum() or character in "_-")
    qth = work_dir / f"{safe_identifier}.qth"
    qth.write_text(
        f"{identifier}\n{site['latitude']}\n{abs(site['longitude'])}\n3m\n",
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
                yield identifier, latitude, longitude, path_loss

    cells = aggregate_points(samples(), bounds, cell_km)
    return cells, {
        "observer_id": identifier,
        "long_name": site["long_name"],
        "latitude": site["latitude"],
        "longitude": site["longitude"],
        "raw_sample_count": raw_sample_count,
        "published_cell_count": len(cells),
        "model_log_tail": completed.stdout.strip().splitlines()[-1:] or [],
    }


def publish_build(
    output_root: Path, payload: dict[str, Any], metadata: dict[str, Any], keep: int = 3
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
    # Validate both files before atomically publishing the new build.
    json.loads((build / "predicted.geojson").read_text(encoding="utf-8"))
    json.loads((build / "metadata.json").read_text(encoding="utf-8"))
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
    for south, west in sorted(terrain_tiles(sites, args.radius_km)):
        ensure_terrain_tile(south, west, terrain_dir)
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    site_results = []
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
        "bounds": list(args.bounds),
        "collector_count": len(sites),
        "cell_count": len(cells),
        "band_counts": band_counts,
        "collectors": site_results,
        "disclaimer": (
            "Terrain-based direct-link estimate. Buildings, foliage, interference, antenna "
            "orientation, and installation quality are not modeled."
        ),
    }
    payload = cells_to_geojson(cells, args.bounds, metadata)
    return publish_build(output_root, payload, metadata)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="atlas-coverage-builder")
    parser.add_argument("--output", default="/var/lib/atlas/coverage")
    parser.add_argument(
        "--observers-url", default="http://127.0.0.1:18081/api/v1/observer-health"
    )
    parser.add_argument("--radius-km", type=float, default=80.0)
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
