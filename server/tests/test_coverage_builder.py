import math

from atlas_server.coverage_builder import (
    aggregate_points,
    blend_reachability_evidence,
    cells_to_geojson,
    cells_to_png,
    classify_path_loss,
    parse_ano,
    recommend_coverage_opportunities,
    sdf_name,
    select_secondary_anchors,
    terrain_tiles,
)


def test_parse_ano_converts_degrees_west_to_negative_longitude():
    rows = list(parse_ano([
        "95, 94 ; bounds\n",
        "37.1000, 94.5000, 320.0, -0.1, 138.25 *\n",
    ]))
    assert rows == [(37.1, -94.5, 138.25)]


def test_path_loss_bands_are_conservative():
    assert classify_path_loss(131) == "strong"
    assert classify_path_loss(141) == "likely"
    assert classify_path_loss(151) == "marginal"
    assert classify_path_loss(151.1) is None


def test_aggregation_keeps_best_collector_per_cell():
    bounds = (-95.0, 36.0, -93.0, 38.0)
    cells = aggregate_points(
        [
            ("ONE", 37.0, -94.0, 140.0),
            ("TWO", 37.0, -94.0, 130.0),
            ("OUT", 39.0, -94.0, 100.0),
        ],
        bounds,
        1.0,
    )
    assert len(cells) == 1
    cell = next(iter(cells.values()))
    assert cell["observer_id"] == "TWO"
    payload = cells_to_geojson(cells, bounds, {"model": "test"})
    assert payload["features"][0]["properties"]["band"] == "strong"
    raster, width, height = cells_to_png(cells, bounds)
    assert raster.startswith(b"\x89PNG\r\n\x1a\n")
    assert width > 0
    assert height > 0


def test_terrain_tile_names_and_radius_selection():
    assert sdf_name(37, -95) == "37:38:94:95.sdf"
    tiles = terrain_tiles([{"latitude": 37.1, "longitude": -94.5}], 10)
    assert (37, -95) in tiles


def test_repeated_reachability_calibrates_without_treating_single_activity_as_coverage():
    bounds = (-95.0, 36.0, -93.0, 38.0)
    cells = aggregate_points([("ONE", 37.0, -94.0, 145.0)], bounds, 1.0)
    features = []
    for index in range(3):
        features.append({
            "type": "Feature",
            "properties": {
                "observer_id": f"OBS{index}", "transmitter_node": 100,
                "hops": 2, "weight": 0.4,
            },
            "geometry": {"type": "Point", "coordinates": [-93.5, 37.5]},
        })
    features.append({
        "type": "Feature",
        "properties": {
            "observer_id": "ONE", "transmitter_node": 200, "hops": 1, "weight": 1.0,
        },
        "geometry": {"type": "Point", "coordinates": [-93.7, 37.7]},
    })
    result = blend_reachability_evidence(
        cells,
        {"type": "FeatureCollection", "features": features, "metadata": {"window_days": 30}},
        bounds,
        1.0,
    )
    assert result["qualified_cells"] == 1
    assert result["extended_cells"] == 1
    assert result["direct_halo_cells"] == 0
    evidence_cells = [cell for cell in cells.values() if cell.get("evidence_role") == "extended"]
    assert len(evidence_cells) == 1
    assert evidence_cells[0]["band"] == "marginal"


def test_repeated_direct_evidence_reinforces_and_adds_conservative_halo():
    bounds = (-95.0, 36.0, -93.0, 38.0)
    cells = aggregate_points([("ONE", 37.0, -94.0, 145.0)], bounds, 1.0)
    features = [{
        "type": "Feature",
        "properties": {
            "observer_id": "ONE", "transmitter_node": 100,
            "hops": 0, "weight": 1.0,
        },
        "geometry": {"type": "Point", "coordinates": [-94.0, 37.0]},
    } for _ in range(3)]
    result = blend_reachability_evidence(
        cells,
        {"type": "FeatureCollection", "features": features, "metadata": {"window_days": 30}},
        bounds,
        1.0,
    )
    assert result["reinforced_cells"] == 1
    assert result["direct_halo_cells"] == 4
    center = next(cell for cell in cells.values() if cell.get("evidence_role") == "reinforced")
    assert center["terrain_band"] == "marginal"
    assert center["band"] == "likely"


def test_dense_multihop_evidence_adds_only_a_marginal_local_footprint():
    bounds = (-95.0, 36.0, -93.0, 38.0)
    cells = {}
    features = [{
        "type": "Feature",
        "properties": {
            "observer_id": f"OBS{index % 2}", "transmitter_node": 100,
            "hops": 2, "weight": 0.4,
        },
        "geometry": {"type": "Point", "coordinates": [-94.0, 37.0]},
    } for index in range(6)]
    result = blend_reachability_evidence(
        cells,
        {"type": "FeatureCollection", "features": features, "metadata": {"window_days": 30}},
        bounds,
        1.0,
    )
    assert result["extended_cells"] == 1
    assert result["reachability_halo_cells"] == 8
    assert len(cells) == 9
    assert {cell["band"] for cell in cells.values()} == {"marginal"}


def test_secondary_anchors_require_stability_support_and_geographic_separation():
    def features(node_num, longitude, latitude, count=8, observer_count=2, moving=False):
        return [{
            "type": "Feature",
            "properties": {
                "observer_id": f"OBS{index % observer_count}",
                "transmitter_node": node_num,
                "hops": 2,
                "weight": 0.5,
                "age_seconds": (index % 2) * 86400,
            },
            "geometry": {
                "type": "Point",
                "coordinates": [longitude + (0.1 if moving and index == 0 else 0), latitude],
            },
        } for index in range(count)]

    payload = {"type": "FeatureCollection", "features": [
        *features(100, -94.0, 37.0),
        *features(200, -93.98, 37.0),  # Too close to the stronger first candidate.
        *features(300, -93.5, 37.0, moving=True),
        *features(400, -93.2, 37.0, count=2),
    ]}
    anchors = select_secondary_anchors(
        payload,
        [{
            "observer_id": "PRIMARY", "node_num": 1, "long_name": "Primary",
            "latitude": 36.5, "longitude": -94.5,
        }],
        (-95.0, 36.0, -93.0, 38.0),
        maximum=8,
        minimum_separation_km=12,
    )
    assert [anchor["node_num"] for anchor in anchors] == [100]
    assert anchors[0]["support_count"] == 8
    assert anchors[0]["day_count"] == 2
    assert anchors[0]["position_spread_km"] == 0


def test_coverage_opportunities_are_uncovered_capped_and_evidence_supported():
    bounds = (-95.0, 36.0, -93.0, 38.0)
    cell_km = 1.0
    lat_step = cell_km / 111.32
    lon_step = cell_km / (111.32 * math.cos(math.radians(37.0)))
    evidence_row, evidence_column = 100, 100
    latitude = bounds[1] + (evidence_row + 0.5) * lat_step
    longitude = bounds[0] + (evidence_column + 0.5) * lon_step
    features = [{
        "type": "Feature",
        "properties": {
            "observer_id": f"OBS{index % 2}",
            "transmitter_node": 100 + index % 3,
            "hops": 2,
            "weight": 0.6,
        },
        "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
    } for index in range(12)]
    cells = {}
    for row_offset, column_offset in ((-4, 0), (4, 0), (0, -4), (0, 4)):
        key = (evidence_row + row_offset, evidence_column + column_offset)
        cells[key] = {"band": "marginal"}
    opportunities = recommend_coverage_opportunities(
        cells,
        {"type": "FeatureCollection", "features": features},
        bounds,
        cell_km,
        maximum=1,
        minimum_separation_km=5,
        regional_additions=1,
        regional_minimum_separation_km=0.5,
        regional_bounds=bounds,
        elevation_lookup=lambda latitude, longitude: 300 + latitude + longitude,
    )
    assert len(opportunities) == 2
    assert opportunities[0]["regional_infill"] is False
    assert opportunities[1]["regional_infill"] is True
    assert all(item["observer_count"] == 2 for item in opportunities)
    assert all(item["node_count"] == 3 for item in opportunities)
    assert all(item["nearest_predicted_coverage_km"] >= 2 for item in opportunities)
    assert all(item["terrain_considered"] is True for item in opportunities)
    assert all(isinstance(item.get("elevation_m"), float) for item in opportunities)
