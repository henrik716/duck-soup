"""The geometry clean-up steps: simplify, snap_to_grid, clean_coverage, fill_holes,
eliminate_slivers, split_lines, explode and remove_duplicates."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from duck_soup.config import load_config_dict
from duck_soup.engine import preview_config_pipeline, run_config


def _poly(*ring: tuple[float, float]) -> dict:
    return {"type": "Polygon", "coordinates": [[list(p) for p in (*ring, ring[0])]]}


def _square(x0: float, y0: float, x1: float, y1: float) -> dict:
    return _poly((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def _line(*coords: tuple[float, float]) -> dict:
    return {"type": "LineString", "coordinates": [list(c) for c in coords]}


def _geojson(path: Path, features: list) -> str:
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": name}, "geometry": geom} for name, geom in features
    ]}), encoding="utf-8")
    return str(path)


def _config(tmp_path: Path, base: list, steps: list[dict], *, mapping: list[dict] | None = None,
            more: dict[str, list] | None = None, **pipeline):
    """A pipeline over `base` ([(name, geometry)]), with `more` ({id: features}) as further
    sources; the mapping defaults to the name, the geometry as WKT, its area and its vertex
    count."""
    if mapping is None:
        mapping = [
            {"to": "name", "from": "name"},
            {"to": "wkt", "expr": "ST_AsText(geom)"},
            {"to": "area", "expr": "round(ST_Area(geom), 6)"},
            {"to": "points", "expr": "ST_NPoints(geom)"},
        ]
    return load_config_dict({
        "name": "t", "working_crs": pipeline.pop("working_crs", "EPSG:25833"),
        "sources": [
            {"id": sid, "format": "geojson", "uri": _geojson(tmp_path / f"{sid}.geojson", feats),
             "crs": pipeline.pop("base_crs", "EPSG:25833") if sid == "base" else "EPSG:25833"}
            for sid, feats in {"base": base, **(more or {})}.items()
        ],
        "base": "base", "steps": steps, "mapping": mapping,
        "output": {"path": str(tmp_path / "out.gpkg"), "layer": "out", "crs": "EPSG:25833"},
        **pipeline,
    })


def _rows(tmp_path, base, steps, **kw) -> list[dict]:
    rows = preview_config_pipeline(_config(tmp_path, base, steps, **kw), limit=100)
    return [{k: v for k, v in r.items() if k != "__geojson"} for r in rows]


def _layers(path: str) -> dict[str, int]:
    con = sqlite3.connect(path)
    try:
        names = [r[0] for r in con.execute("SELECT table_name FROM gpkg_contents")]
        return {n: con.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        con.close()


# Two squares sharing the edge x = 10, which wiggles (10.1, 3), (9.9, 6), and a small bump
# (5, -0.1) on the left square's outer edge.
_LEFT = _poly((0, 0), (5, -0.1), (10, 0), (10.1, 3), (9.9, 6), (10, 10), (0, 10))
_RIGHT = _poly((10, 0), (20, 0), (20, 10), (10, 10), (9.9, 6), (10.1, 3))
_PAIR = [("left", _LEFT), ("right", _RIGHT)]


def _shared_edge_stays_shared(tmp_path, step) -> list[dict]:
    rows = _rows(tmp_path, _PAIR, [step], mapping=[
        {"to": "name", "from": "name"},
        {"to": "wkt", "expr": "ST_AsText(geom)"},
        {"to": "area", "expr": "ST_Area(geom)"},
    ])
    assert all("10.1" not in r["wkt"] and "9.9" not in r["wkt"] for r in rows)
    return rows


def test_simplify_one_feature_at_a_time(tmp_path):
    zigzag = _line((0, 0), (5, 0.01), (10, 0), (15, -0.01), (20, 0))
    rows = _rows(tmp_path, [("z", zigzag), ("sq", _square(0, 0, 1, 1)), ("p", {"type": "Point", "coordinates": [1, 1]})],
                 [{"type": "simplify", "tolerance": 0.1}])
    assert [(r["name"], r["points"]) for r in rows] == [("z", 2), ("sq", 5), ("p", 1)]


def test_simplify_shared_boundaries_keeps_neighbours_fitting(tmp_path):
    rows = _shared_edge_stays_shared(tmp_path, {"type": "simplify", "tolerance": 2, "shared_boundaries": True})
    # No gap or overlap opened up: the two still cover exactly the 20 x 10 rectangle.
    assert sum(r["area"] for r in rows) == pytest.approx(200, abs=1e-9)
    assert "-0.1" not in rows[0]["wkt"]  # the outer bump is simplified too


def test_simplify_can_keep_the_outer_boundary(tmp_path):
    rows = _shared_edge_stays_shared(
        tmp_path, {"type": "simplify", "tolerance": 2, "shared_boundaries": True, "keep_outer_boundary": True},
    )
    assert "5 -0.1" in rows[0]["wkt"]


def test_simplify_shared_boundaries_keeps_multipolygons_and_other_rows(tmp_path):
    multi = {"type": "MultiPolygon", "coordinates": [_square(30, 0, 40, 10)["coordinates"], _square(50, 0, 60, 10)["coordinates"]]}
    base = [*_PAIR, ("multi", multi), ("line", _line((0, 20), (5, 20.01), (10, 20))), ("none", None)]
    rows = _rows(tmp_path, base, [{"type": "simplify", "tolerance": 2, "shared_boundaries": True}])
    assert [r["name"] for r in rows] == ["left", "right", "multi", "line", "none"]
    assert rows[2]["wkt"].startswith("MULTIPOLYGON") and rows[2]["area"] == 200
    assert rows[3]["points"] == 2  # a line is simplified on its own
    assert rows[4]["wkt"] is None


def test_snap_to_grid_rounds_vertices_and_keeps_collapsed_rows(tmp_path):
    base = [("a", _line((0.12, 0.38), (9.87, 0.41))), ("tiny", _square(0.1, 0.1, 0.2, 0.2))]
    rows = _rows(tmp_path, base, [{"type": "snap_to_grid", "grid_size": 0.5}])
    assert [(r["name"], r["wkt"]) for r in rows] == [("a", "LINESTRING (0 0.5, 10 0.5)"), ("tiny", "POLYGON EMPTY")]


def test_clean_coverage_removes_overlaps_and_fills_gaps(tmp_path):
    # left and right overlap by 1; the strips above and below close a 0.2 wide gap
    # between right and far into a hole.
    base = [
        ("left", _square(0, 0, 6, 10)), ("right", _square(5, 0, 10, 10)), ("far", _square(10.2, 0, 15, 10)),
        ("top", _square(0, 10, 15, 11)), ("bottom", _square(0, -1, 15, 0)),
    ]
    step = {"type": "clean_coverage"}
    overlapping = sum(r["area"] for r in _rows(tmp_path, base, []))
    assert overlapping == 60 + 50 + 48 + 15 + 15
    assert sum(r["area"] for r in _rows(tmp_path, base, [step])) == pytest.approx(overlapping - 10 - 0)
    filled = _rows(tmp_path, base, [{**step, "max_gap_width": 0.5}])
    assert sum(r["area"] for r in filled) == pytest.approx(15 * 12)
    assert [r["name"] for r in filled] == ["left", "right", "far", "top", "bottom"]


def test_clean_coverage_snaps_nearly_matching_edges(tmp_path):
    base = [("a", _square(0, 0, 1, 1)), ("b", _square(1.01, 0, 2, 1))]
    rows = _rows(tmp_path, base, [{"type": "clean_coverage", "snap_distance": 0.05}])
    assert sum(r["area"] for r in rows) == pytest.approx(2)


def test_clean_coverage_leaves_a_covered_polygon_empty(tmp_path):
    rows = _rows(tmp_path, [("big", _square(0, 0, 2, 2)), ("inside", _square(0.5, 0.5, 1, 1))], [{"type": "clean_coverage"}])
    assert [(r["name"], r["area"]) for r in rows] == [("big", 4), ("inside", 0)]


def test_explode_gives_a_row_per_part(tmp_path):
    multi = {"type": "MultiPolygon", "coordinates": [_square(0, 0, 1, 1)["coordinates"], _square(5, 5, 7, 7)["coordinates"]]}
    base = [("m", multi), ("single", _square(0, 0, 3, 3)), ("none", None)]
    mapping = [{"to": "name", "from": "name"}, {"to": "part", "from": "part"}, {"to": "area", "expr": "ST_Area(geom)"}]
    cfg = _config(tmp_path, base, [{"type": "explode", "part_field": "part"}], mapping=mapping)
    rows = [(r["name"], r["part"], r["area"]) for r in preview_config_pipeline(cfg, limit=10)]
    assert rows == [("m", 1, 1), ("m", 2, 4), ("single", 1, 9), ("none", 1, None)]
    assert _layers(run_config(cfg)) == {"out": 4}


def test_explode_part_field_replaces_a_column_of_that_name(tmp_path):
    multi = {"type": "MultiPolygon", "coordinates": [_square(0, 0, 1, 1)["coordinates"], _square(5, 5, 7, 7)["coordinates"]]}
    steps = [{"type": "explode", "part_field": "name"}]
    rows = _rows(tmp_path, [("m", multi)], steps, mapping=[{"to": "name", "from": "name"}])
    assert [r["name"] for r in rows] == [1, 2]


def test_explode_numbers_rows_for_later_joins(tmp_path):
    # A join's "first match" ranks by __src_row: the parts must keep distinct ones.
    multi = {"type": "MultiPolygon", "coordinates": [_square(0, 0, 1, 1)["coordinates"], _square(5, 5, 7, 7)["coordinates"]]}
    steps = [
        {"type": "explode", "part_field": "part"}, {"type": "snapshot", "id": "parts"},
        {"type": "spatial_join", "source": "parts", "fields": {"hit": "part"}},
    ]
    rows = _rows(tmp_path, [("m", multi)], steps, mapping=[{"to": "part", "from": "part"}, {"to": "hit", "from": "hit"}])
    assert [(r["part"], r["hit"]) for r in rows] == [(1, 1), (2, 2)]


def test_remove_duplicates_by_columns_keeps_the_first_and_rejects_the_rest(tmp_path):
    base = [("a", _square(0, 0, 1, 1)), ("b", _square(0, 0, 2, 2)), ("a", _square(0, 0, 3, 3))]
    step = {"type": "remove_duplicates", "by": ["name"], "rejects": "dups"}
    rows = _rows(tmp_path, base, [step])
    assert [(r["name"], r["area"]) for r in rows] == [("a", 1), ("b", 4)]
    assert _layers(run_config(_config(tmp_path, base, [step]))) == {"out": 2, "dups": 1}


def test_remove_duplicates_by_geometry_ignores_start_vertex_and_direction(tmp_path):
    base = [
        ("sq", _square(0, 0, 1, 1)), ("sq_rotated", _poly((1, 1), (0, 1), (0, 0), (1, 0))),
        ("ln", _line((0, 0), (5, 5))), ("ln_reversed", _line((5, 5), (0, 0))), ("other", _square(0, 0, 2, 2)),
    ]
    rows = _rows(tmp_path, base, [{"type": "remove_duplicates", "geometry": True}])
    assert [r["name"] for r in rows] == ["sq", "ln", "other"]
    # with a column too, both must match
    both = _rows(tmp_path, [*base, ("sq", _square(0, 0, 1, 1))], [{"type": "remove_duplicates", "by": ["name"], "geometry": True}])
    assert [r["name"] for r in both] == ["sq", "sq_rotated", "ln", "ln_reversed", "other"]


# A 10 x 10 square with a 1 x 1 hole and a 4 x 4 one.
_DONUT = {"type": "Polygon", "coordinates": [
    _square(0, 0, 10, 10)["coordinates"][0], _square(1, 1, 2, 2)["coordinates"][0],
    _square(4, 4, 8, 8)["coordinates"][0],
]}


def test_fill_holes_fills_every_hole_or_the_small_ones(tmp_path):
    multi = {"type": "MultiPolygon", "coordinates": [_DONUT["coordinates"], _square(20, 0, 21, 1)["coordinates"]]}
    base = [("donut", _DONUT), ("multi", multi), ("line", _line((0, 0), (1, 1))), ("none", None)]
    rows = _rows(tmp_path, base, [{"type": "fill_holes"}])
    assert [(r["name"], r["area"]) for r in rows] == [("donut", 100), ("multi", 101), ("line", 0), ("none", None)]
    assert rows[1]["wkt"].startswith("MULTIPOLYGON")
    small = _rows(tmp_path, base, [{"type": "fill_holes", "max_area": 5}])
    assert [r["area"] for r in small[:2]] == [100 - 16, 101 - 16]


# Two neighbours and a 0.5 wide sliver along their shared corner region: it touches `big`
# along 7 units (x = 10, y 3..10) and `wide` along 0.5 (y = 3).
_SLIVER_SET = [
    ("big", _square(0, 0, 10, 10)), ("wide", _square(10, 0, 30, 3)),
    ("sliver", _square(10, 3, 10.5, 10)), ("lonely", _square(50, 50, 50.1, 50.1)),
]


def test_eliminate_slivers_merges_into_the_longest_border(tmp_path):
    rows = _rows(tmp_path, _SLIVER_SET, [{"type": "eliminate_slivers", "max_area": 5}])
    assert [(r["name"], r["area"]) for r in rows] == [("big", 103.5), ("wide", 60), ("lonely", 0.01)]


def test_eliminate_slivers_can_merge_into_the_largest_neighbour(tmp_path):
    # This sliver runs 20 along `wide` (60) and only 0.5 along `big` (100).
    base = [("big", _square(0, 0, 10, 10)), ("wide", _square(10, 0, 30, 3)), ("sliver", _square(10, 3, 30, 3.5))]
    step = {"type": "eliminate_slivers", "max_area": 11}
    longest = _rows(tmp_path, base, [step])
    assert [(r["name"], r["area"]) for r in longest] == [("big", 100), ("wide", 70)]
    largest = _rows(tmp_path, base, [{**step, "merge_into": "largest"}])
    assert [(r["name"], r["area"]) for r in largest] == [("big", 110), ("wide", 60)]


def test_eliminate_slivers_picks_by_condition_and_only_merges_into_the_rest(tmp_path):
    # Picked by name, `big` is a sliver too: both go into `wide`, the one neighbour left.
    picked = _rows(tmp_path, _SLIVER_SET, [{"type": "eliminate_slivers", "where": "name IN ('big', 'sliver')"}])
    assert [(r["name"], r["area"]) for r in picked] == [("wide", 163.5), ("lonely", 0.01)]
    # With max_area too, both must hold: `big` is too large.
    both = _rows(tmp_path, _SLIVER_SET, [{"type": "eliminate_slivers", "max_area": 5, "where": "name IN ('big', 'sliver')"}])
    assert [(r["name"], r["area"]) for r in both] == [("big", 103.5), ("wide", 60), ("lonely", 0.01)]


def test_split_lines_cuts_where_lines_cross_or_touch(tmp_path):
    base = [
        ("h", _line((0, 0), (10, 0))), ("v", _line((5, -5), (5, 5))),
        ("t", _line((8, 0), (8, 4))),  # ends on h: cuts h, isn't cut itself
        ("far", _line((0, 20), (5, 20))), ("pt", {"type": "Point", "coordinates": [1, 1]}),
    ]
    rows = _rows(tmp_path, base, [{"type": "split_lines"}])
    assert [(r["name"], r["wkt"]) for r in rows] == [
        ("h", "LINESTRING (0 0, 5 0)"), ("h", "LINESTRING (5 0, 8 0)"), ("h", "LINESTRING (8 0, 10 0)"),
        ("v", "LINESTRING (5 -5, 5 0)"), ("v", "LINESTRING (5 0, 5 5)"),
        ("t", "LINESTRING (8 0, 8 4)"), ("far", "LINESTRING (0 20, 5 20)"), ("pt", "POINT (1 1)"),
    ]


def test_split_lines_cuts_at_shared_stretches_and_keeps_vertices(tmp_path):
    # b runs along a from x 4 to 6: a is cut at both ends of the shared stretch
    base = [("a", _line((0, 0), (3, 0), (10, 0))), ("b", _line((4, 0), (6, 0), (6, 5)))]
    rows = _rows(tmp_path, base, [{"type": "split_lines"}])
    assert [(r["name"], r["wkt"]) for r in rows if r["name"] == "a"] == [
        ("a", "LINESTRING (0 0, 3 0, 4 0)"), ("a", "LINESTRING (4 0, 6 0)"), ("a", "LINESTRING (6 0, 10 0)"),
    ]


def test_split_lines_tolerance_cuts_at_line_ends_that_just_miss(tmp_path):
    # b stops 2 mm short of a, c ends 3 mm past it: exact noding cuts at neither
    base = [("a", _line((0, 0), (10, 0))), ("b", _line((4, 5), (4, 0.002))), ("c", _line((7, 5), (7, -0.003)))]
    exact = _rows(tmp_path, base, [{"type": "split_lines"}])
    assert [r["wkt"] for r in exact if r["name"] == "a"] == ["LINESTRING (0 0, 7 0)", "LINESTRING (7 0, 10 0)"]
    near = _rows(tmp_path, base, [{"type": "split_lines", "tolerance": 0.01}])
    assert [r["wkt"] for r in near if r["name"] == "a"] == [
        "LINESTRING (0 0, 4 0)", "LINESTRING (4 0, 7 0)", "LINESTRING (7 0, 10 0)",
    ]
    # b itself isn't moved or cut
    assert [r["wkt"] for r in near if r["name"] == "b"] == ["LINESTRING (4 5, 4 0.002)"]


def test_split_lines_by_a_source_cuts_at_polygon_edges(tmp_path):
    rows = _rows(tmp_path, [("h", _line((0, 0), (10, 0)))], [{"type": "split_lines", "source": "zones"}],
                 more={"zones": [("z", _square(3, -1, 6, 1))]})
    assert [r["wkt"] for r in rows] == ["LINESTRING (0 0, 3 0)", "LINESTRING (3 0, 6 0)", "LINESTRING (6 0, 10 0)"]


def test_split_lines_numbers_rows_for_later_joins(tmp_path):
    base = [("h", _line((0, 0), (10, 0))), ("v", _line((5, -5), (5, 5)))]
    steps = [
        {"type": "split_lines"},
        {"type": "attributes", "columns": [{"to": "piece", "expr": "round(ST_XMin(geom) + ST_YMin(geom))"}]},
        {"type": "snapshot", "id": "pieces"},
        {"type": "spatial_join", "source": "pieces", "predicate": "within", "fields": {"same": "piece"}},
    ]
    rows = _rows(tmp_path, base, steps, mapping=[{"to": "piece", "from": "piece"}, {"to": "same", "from": "same"}])
    assert all(r["piece"] == r["same"] for r in rows) and len(rows) == 4


@pytest.mark.parametrize("step,message", [
    ({"type": "simplify", "tolerance": 1, "keep_outer_boundary": True}, "needs shared_boundaries"),
    ({"type": "simplify", "tolerance": 0}, "greater than 0"),
    ({"type": "snap_to_grid", "grid_size": -1}, "greater than 0"),
    ({"type": "remove_duplicates"}, "list the columns to compare"),
    ({"type": "explode", "part_field": "geom"}, "reserved"),
    ({"type": "eliminate_slivers"}, "give max_area, or a where condition"),
    ({"type": "eliminate_slivers", "where": "  "}, "give max_area, or a where condition"),
    ({"type": "fill_holes", "max_area": 0}, "greater than 0"),
    ({"type": "split_lines", "source": "nowhere"}, "unknown source 'nowhere'"),
])
def test_invalid_steps_are_refused(tmp_path, step, message):
    with pytest.raises(ValueError, match=message):
        _config(tmp_path, [("a", _square(0, 0, 1, 1))], [step])


def test_blank_fields_mean_none(tmp_path):
    steps = [{"type": "explode", "part_field": " "}, {"type": "split_lines", "source": ""}]
    explode, split = _config(tmp_path, [("a", _square(0, 0, 1, 1))], steps).pipelines[0].steps
    assert explode.part_field is None and split.source is None


@pytest.mark.parametrize("step", [
    {"type": "simplify", "tolerance": 0.001},
    {"type": "snap_to_grid", "grid_size": 0.001},
    {"type": "clean_coverage", "max_gap_width": 0.001},
    {"type": "fill_holes", "max_area": 0.001},
    {"type": "eliminate_slivers", "max_area": 0.001},
    {"type": "split_lines", "tolerance": 0.001},
])
def test_distances_need_a_projected_working_crs(tmp_path, step):
    cfg = _config(tmp_path, [("a", _square(0, 0, 1, 1))], [step], working_crs="EPSG:4326", base_crs="EPSG:4326")
    with pytest.raises(ValueError, match="is in degrees"):
        preview_config_pipeline(cfg, limit=5)


def test_geometry_steps_need_geometry(tmp_path):
    csv = tmp_path / "rows.csv"
    csv.write_text("name\na\n", encoding="utf-8")
    cfg = load_config_dict({
        "name": "t", "sources": [{"id": "base", "format": "csv", "uri": str(csv)}], "base": "base",
        "steps": [{"type": "explode"}], "mapping": [{"to": "name", "from": "name"}],
        "output": {"path": str(tmp_path / "out.gpkg"), "layer": "out"},
    })
    with pytest.raises(ValueError, match="explode needs geometry"):
        preview_config_pipeline(cfg, limit=5)
