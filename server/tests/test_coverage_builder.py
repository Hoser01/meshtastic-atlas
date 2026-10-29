from atlas_server.coverage_builder import (
    aggregate_points,
    cells_to_geojson,
    classify_path_loss,
    parse_ano,
    sdf_name,
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


def test_terrain_tile_names_and_radius_selection():
    assert sdf_name(37, -95) == "37:38:94:95.sdf"
    tiles = terrain_tiles([{"latitude": 37.1, "longitude": -94.5}], 10)
    assert (37, -95) in tiles
