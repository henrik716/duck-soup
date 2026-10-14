"""End-to-end pipeline execution against the checked-in fixture YAMLs +
data/ files, asserting on the GeoPackage(s) actually written to disk.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from duck_soup import mapping
from duck_soup.config import MAP_FUNCS, CodeCase, CodeList, load_config, load_config_dict, reject_layer_names
from duck_soup.engine import Engine, preview_config_pipeline, run_config

REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINES_DIR = REPO_ROOT / "pipelines"


def _gpkg_layers(path: Path) -> dict[str, int]:
    """{layer_name: row_count} for every feature layer in a GeoPackage."""
    con = sqlite3.connect(str(path))
    try:
        names = [r[0] for r in con.execute("SELECT table_name FROM gpkg_contents").fetchall()]
        return {name: con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in names}
    finally:
        con.close()


@pytest.fixture(autouse=True)
def _repo_root_cwd(monkeypatch):
    # Source `uri`s in the fixture YAMLs are relative (e.g. data/test.geojson),
    # resolved by DuckDB against the process cwd.
    monkeypatch.chdir(REPO_ROOT)


def test_run_single_pipeline_writes_expected_columns(tmp_path):
    cfg = load_config(PIPELINES_DIR / "test.yaml")
    cfg.output = str(tmp_path / "test.gpkg")

    out_path = run_config(cfg)

    assert Path(out_path) == tmp_path / "test.gpkg"
    layers = _gpkg_layers(Path(out_path))
    assert layers == {"output": 5}

    con = sqlite3.connect(out_path)
    try:
        cols = {r[1] for r in con.execute('PRAGMA table_info("output")').fetchall()}
    finally:
        con.close()
    assert {"name", "category", "latitude", "longitude", "mgrs", "uuid", "county"} <= cols


def test_run_multi_layer_pipeline_splits_matched_and_unmatched(tmp_path):
    cfg = load_config(PIPELINES_DIR / "test_multi_layer.yaml")
    cfg.output = str(tmp_path / "multi_layer.gpkg")

    out_path = run_config(cfg)

    layers = _gpkg_layers(Path(out_path))
    assert set(layers) == {"matched", "unmatched"}
    # every base feature ends up in exactly one of the two filtered layers
    assert sum(layers.values()) == 5


def test_run_multi_pipeline_shared_output(tmp_path):
    cfg = load_config(PIPELINES_DIR / "test_multi_pipeline_shared_output.yaml")
    cfg.output = str(tmp_path / "shared.gpkg")

    out_path = run_config(cfg)

    layers = _gpkg_layers(Path(out_path))
    assert set(layers) == {"places_out", "fylke_out"}
    assert layers["places_out"] == 5
    assert layers["fylke_out"] > 0


def test_run_flatgeobuf_source_writes_expected_rows(tmp_path):
    # End-to-end check that `flatgeobuf` sources flow through the full engine (not just
    # read_expr in isolation — see test_sources.py's read_expr-level FlatGeobuf test): GDAL's
    # FlatGeobuf driver is bundled with the spatial extension, so this is the ordinary
    # ST_Read path every other GDAL format (gpkg, geojson, shp, ...) already goes through.
    cfg = load_config_dict({
        "name": "flatgeobuf_test",
        "sources": [
            {"id": "places", "format": "flatgeobuf", "uri": "data/test.fgb", "crs": "EPSG:4326"},
        ],
        "base": "places",
        "mapping": [
            {"to": "name", "from": "name"},
            {"to": "category", "from": "category"},
        ],
        "output": {"path": str(tmp_path / "flatgeobuf.gpkg"), "layer": "output", "crs": "EPSG:25833"},
    })

    out_path = run_config(cfg)

    layers = _gpkg_layers(Path(out_path))
    assert layers == {"output": 5}


def test_preview_pipeline_returns_rows_without_writing_output(tmp_path):
    cfg = load_config(PIPELINES_DIR / "test.yaml")
    cfg.output = str(tmp_path / "unused.gpkg")

    rows = preview_config_pipeline(cfg, pipeline_idx=0, limit=10)

    assert len(rows) == 5
    assert not (tmp_path / "unused.gpkg").exists()
    assert {"name", "category", "county"} <= rows[0].keys()


def test_case_condition_is_blank_matches_null_or_empty_string():
    condition = mapping.case_condition('"category"', CodeCase(is_blank=True, value="Unknown"), True)
    assert condition == '("category" IS NULL OR "category" = \'\')'


def test_codelist_expr_routes_blank_and_specific_value_to_same_output():
    cl = CodeList(
        source="category",
        cases=[
            CodeCase(match="n/a", value="Unknown"),
            CodeCase(is_blank=True, value="Unknown"),
        ],
    )
    expr = mapping.codelist_expr(cl)
    assert "IS NULL" in expr
    assert expr.count("THEN 'Unknown'") == 2


@pytest.mark.parametrize("text, as_date, as_timestamp", [
    ("2024-03-14", "2024-03-14", "2024-03-14 00:00:00"),
    ("2024-03-14 08:30:00", "2024-03-14", "2024-03-14 08:30:00"),
    ("14.03.2024", "2024-03-14", "2024-03-14 00:00:00"),
    ("14.03.2024 08:30", "2024-03-14", "2024-03-14 08:30:00"),
    ("14.03.2024 08:30:15", "2024-03-14", "2024-03-14 08:30:15"),
    ("March 2024", None, None),
])
def test_date_casts_read_iso_and_day_month_year(text, as_date, as_timestamp):
    import duckdb
    con = duckdb.connect()
    value = mapping._lit(text)
    got = con.execute(
        f"SELECT CAST({mapping.cast_expr(value, 'date')} AS VARCHAR), "
        f"CAST({mapping.cast_expr(value, 'TIMESTAMP')} AS VARCHAR)"
    ).fetchone()
    assert got == (as_date, as_timestamp)


def test_every_mapping_func_has_sql():
    # config.MAP_FUNCS validates `func:`; mapping.FUNC_SQL compiles it. A func added to one
    # and not the other would pass validation and then fail to compile, or the reverse.
    assert set(mapping.FUNC_SQL) == set(MAP_FUNCS)
    assert mapping.MEASURE_FUNCS <= mapping.GEOMETRY_FUNCS <= set(MAP_FUNCS)


# -- one-best-match steps (spatial_join / nearest_neighbor / clip / erase) -------------
# Hand-placed geometries in a projected CRS, so every expected match, distance and area
# below can be worked out by hand. Sources are row-ordered as written; "first match"
# means lowest source row.


def _square(x0: float, y0: float, x1: float, y1: float) -> dict:
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def _point(x: float, y: float) -> dict:
    return {"type": "Point", "coordinates": [x, y]}


def _write_geojson(path: Path, features: list[tuple[str, dict]]) -> str:
    import json

    path.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"name": name}, "geometry": geom}
            for name, geom in features
        ],
    }), encoding="utf-8")
    return str(path)


# zone A covers x 0..2, zone B x 1..3 (both y 0..1), so x 1..2 is in both.
_ZONES = [("A", _square(0, 0, 2, 1)), ("B", _square(1, 0, 3, 1))]
_POINTS = [("p1", _point(0.5, 0.5)), ("p2", _point(1.5, 0.5)), ("p3", _point(10, 10)), ("p4", _point(2.5, 0.5))]


def _step_output(
    tmp_path: Path, base: list, other: list, steps: list[dict], sql: str, more: dict | None = None,
) -> list[tuple]:
    """Run `steps` over `base` (with `other` as source `other`, plus any `more` {id: features})
    and return `sql` evaluated over the final step view, referenced as `t`."""
    import duckdb

    from duck_soup.derive import init_duckdb

    sources = [
        {"id": sid, "format": "geojson", "uri": _write_geojson(tmp_path / f"{sid}.geojson", feats),
         "crs": "EPSG:25833"}
        for sid, feats in (("base", base), ("other", other), *(more or {}).items())
    ]
    cfg = load_config_dict({
        "name": "t", "working_crs": "EPSG:25833", "sources": sources, "base": "base",
        "steps": steps, "mapping": [],
        "output": {"path": str(tmp_path / "unused.gpkg"), "layer": "out", "crs": "EPSG:25833"},
    })
    engine = Engine(cfg.pipelines[0])
    con = duckdb.connect()
    init_duckdb(con)
    view = engine._prepare(con, tmp_path)
    return con.execute(sql.replace(" t", f' "{view}"')).fetchall()


def test_spatial_join_summarize_aggregates_each_features_matches(tmp_path):
    zones = [*_ZONES, ("C", _square(20, 20, 21, 21))]  # C contains no point
    fields = {"n": {"how": "count"}, "names": {"from": "name", "how": "all"}, "lowest": {"from": "name", "how": "min"}}
    rows = _step_output(
        tmp_path, zones, _POINTS,
        [{"type": "spatial_join", "source": "other", "match": "summarize", "fields": fields}],
        "SELECT name, n, names, lowest FROM t",
    )
    assert rows == [("A", 2, "p1, p2", "p1"), ("B", 2, "p2, p4", "p2"), ("C", 0, None, None)]
    # with rejects, a feature with no match leaves the chain
    rows = _step_output(
        tmp_path, zones, _POINTS,
        [{"type": "spatial_join", "source": "other", "match": "summarize", "fields": fields, "rejects": "lonely"}],
        "SELECT name FROM t",
    )
    assert rows == [("A",), ("B",)]


def test_attribute_join_first_is_the_first_source_row_and_summarize_takes_them_all(tmp_path):
    lookup = [("p1", _point(0, 0)), ("p1", _point(5, 5)), ("p2", _point(1, 1))]
    first = _step_output(
        tmp_path, _POINTS, lookup,
        [{"type": "attribute_join", "source": "other", "left": "name", "right": "name", "fields": {"g": "geom"}}],
        "SELECT name, ST_AsText(g) FROM t",
    )
    assert first == [("p1", "POINT (0 0)"), ("p2", "POINT (1 1)"), ("p3", None), ("p4", None)]
    summed = _step_output(
        tmp_path, _POINTS, lookup,
        [{"type": "attribute_join", "source": "other", "left": "name", "right": "name", "match": "summarize",
          "fields": {"n": {"how": "count"}, "xs": {"expr": "string_agg(ST_X(geom)::INT::VARCHAR, ',' ORDER BY ST_X(geom))"}}}],
        "SELECT name, n, xs FROM t",
    )
    assert summed == [("p1", 2, "0,5"), ("p2", 1, "1"), ("p3", 0, None), ("p4", 0, None)]


def test_summarized_join_fields_explain_a_column_that_cant_work(tmp_path):
    from duck_soup.engine import MappingError

    with pytest.raises(MappingError) as info:
        _step_output(tmp_path, _ZONES, _POINTS, [{"type": "spatial_join", "source": "other", "match": "summarize",
                     "fields": {"total": {"from": "name", "how": "sum"}, "x": {"from": "colour", "how": "any"}}}], "SELECT 1")
    head, path, _ = str(info.value).split("\n")
    assert head == "step 1 field 'total' (sum): name is text, and sum only works on numbers. Use one value, most common or all of them instead"
    assert path == "pipelines.0.steps.0.fields.total"
    with pytest.raises(MappingError, match="there's no column 'colour' in 'other'"):
        _step_output(tmp_path, _ZONES, _POINTS, [{"type": "spatial_join", "source": "other", "match": "summarize",
                     "fields": {"x": {"from": "colour", "how": "any"}}}], "SELECT 1")


def test_spatial_join_first_match_keeps_base_order_and_lowest_source_row(tmp_path):
    rows = _step_output(
        tmp_path, _POINTS, _ZONES,
        [{"type": "spatial_join", "source": "other", "fields": {"zone": "name"}}],
        "SELECT name, zone FROM t",
    )
    # p2 is in both zones -> A (first source row); p3 matches nothing but is kept.
    assert rows == [("p1", "A"), ("p2", "A"), ("p3", None), ("p4", "B")]


def test_spatial_join_largest_overlap_picks_biggest_intersection(tmp_path):
    # Overlaps A by 0.8 and B by 1.8: "first" would say A, largest_overlap says B.
    base = [("q", _square(1.2, 0, 3, 1))]
    step = {"type": "spatial_join", "source": "other", "fields": {"zone": "name"}}
    first = _step_output(tmp_path, base, _ZONES, [step], "SELECT zone FROM t")
    largest = _step_output(
        tmp_path, base, _ZONES, [{**step, "on_multiple": "largest_overlap"}], "SELECT zone FROM t"
    )
    assert (first, largest) == ([("A",)], [("B",)])


def test_nearest_neighbor_picks_closest_and_respects_max_distance(tmp_path):
    # s1 and s1_dup share a location: the tie goes to the earlier source row.
    stations = [("s1", _point(0, 0)), ("s2", _point(3, 0.5)), ("s1_dup", _point(0, 0))]
    step = {"type": "nearest_neighbor", "source": "other", "fields": {"station": "name"},
            "distance_field": "dist"}
    sql = "SELECT name, station, round(dist, 3) FROM t"

    unbounded = _step_output(tmp_path, _POINTS, stations, [step], sql)
    assert unbounded == [
        ("p1", "s1", 0.707), ("p2", "s2", 1.5), ("p3", "s2", 11.8), ("p4", "s2", 0.5),
    ]
    bounded = _step_output(tmp_path, _POINTS, stations, [{**step, "max_distance": 5}], sql)
    assert bounded == unbounded[:2] + [("p3", None, None)] + unbounded[3:]


def test_clip_uses_first_match_and_drops_unmatched_and_touching(tmp_path):
    base = [
        ("q", _square(1.2, 0, 3, 1)),
        ("far", _square(50, 50, 51, 51)),
        ("edge", _square(3, 0, 4, 1)),  # shares only an edge with zone B
        ("corner", _square(3, 1, 4, 2)),  # shares only a corner with zone B
    ]
    rows = _step_output(
        tmp_path, base, _ZONES, [{"type": "clip", "source": "other"}],
        "SELECT name, ST_GeometryType(geom), round(ST_Area(geom), 3) FROM t",
    )
    # clipped to zone A (first row), not B; "far" matches nothing, and "edge"/"corner"
    # would only leave a line/point sliver in a polygon layer, so all three are dropped.
    assert rows == [("q", "POLYGON", 0.8)]


def test_intersect_overlay_drops_touching_only_pairs(tmp_path):
    base = [("q", _square(1.2, 0, 3, 1)), ("edge", _square(3, 0, 4, 1))]
    rows = _step_output(
        tmp_path, base, _ZONES,
        [{"type": "intersect_overlay", "source": "other", "fields": {"zone": "name"}}],
        "SELECT name, zone, ST_GeometryType(geom), round(ST_Area(geom), 3) FROM t ORDER BY zone",
    )
    assert rows == [("q", "A", "POLYGON", 0.8), ("q", "B", "POLYGON", 1.8)]


def test_erase_subtracts_union_of_all_matches_and_drops_fully_erased(tmp_path):
    base = [
        ("q", _square(1.2, 0, 4, 1)),
        ("far", _square(50, 50, 51, 51)),
        ("covered", _square(0.5, 0.2, 2.5, 0.8)),  # entirely inside A ∪ B
    ]
    rows = _step_output(
        tmp_path, base, _ZONES, [{"type": "erase", "source": "other"}],
        "SELECT name, round(ST_Area(geom), 3) FROM t",
    )
    # A ∪ B covers x 0..3, leaving x 3..4 of q; "far" matches nothing and passes through;
    # "covered" has nothing left and is dropped rather than kept as an empty geometry.
    assert rows == [("q", 1.0), ("far", 1.0)]


def _line(*coords: tuple[float, float]) -> dict:
    return {"type": "LineString", "coordinates": [list(c) for c in coords]}


def test_line_overlay_chains_into_a_complete_split_network(tmp_path):
    base = [("main", _line((0, 0), (10, 0), (20, 0), (30, 0))), ("side", _line((0, 10), (10, 10)))]
    ds2 = [("x", _line((5, 0), (15, 0)))]
    ds3 = [("y", _line((12, 0), (25, 0))), ("cross", _line((8, -5), (8, 5)))]
    rows = _step_output(
        tmp_path, base, ds2,
        [
            {"type": "line_overlay", "source": "other", "fields": {"ds2": "name"}},
            {"type": "line_overlay", "source": "ds3", "fields": {"ds3": "name"}},
        ],
        "SELECT name, ds2, ds3, ST_GeometryType(geom), round(ST_XMin(geom), 6), round(ST_Length(geom), 6) "
        "FROM t ORDER BY name, ST_XMin(geom)",
        more={"ds3": ds3},
    )
    # x covers the middle of main, so both ends come back as separate features; "cross"
    # only crosses main, so it neither adds a piece nor cuts 5..12 at x=8.
    assert rows == [
        ("main", None, None, "LINESTRING", 0, 5),
        ("main", "x", None, "LINESTRING", 5, 7),
        ("main", "x", "y", "LINESTRING", 12, 3),
        ("main", None, "y", "LINESTRING", 15, 10),
        ("main", None, None, "LINESTRING", 25, 5),
        ("side", None, None, "LINESTRING", 0, 10),
    ]


def test_line_overlay_tolerance_catches_nearly_coincident_lines(tmp_path):
    # Sloped, with the overlay's vertices a few millimetres off the base line and one of them
    # in the middle of a base segment, the way two datasets digitised from the same line
    # differ. Snapping both to a grid doesn't line those up; the tolerance has to.
    base = [("main", _line((0, 600), (500, 490), (1000, 380)))]
    other = [("x", _line((200, 556.004), (500.003, 489.998), (800.002, 424.003)))]

    def run(**extra):
        return _step_output(
            tmp_path, base, other,
            [{"type": "line_overlay", "source": "other", "fields": {"ds2": "name"}, **extra}],
            "SELECT ds2, round(ST_XMin(geom)), round(ST_XMax(geom)), ST_Length(geom) FROM t ORDER BY ST_XMin(geom)",
        )

    # Without one, only the few centimetres where the lines cross each other count as shared.
    assert sum(length for ds2, _, _, length in run() if ds2) < 1
    rows = run(tolerance=0.01)
    assert [r[:3] for r in rows] == [(None, 0, 200), ("x", 200, 800), (None, 800, 1000)]
    assert sum(r[3] for r in rows) == pytest.approx(sum(r[3] for r in run()))


def test_line_overlay_pieces_share_end_points_and_keep_base_geometry(tmp_path):
    base = [("main", _line((0, 0), (10, 0), (20, 0)))]
    other = [("a", _line((2, 0.003), (8, -0.002))), ("b", _line((8, 0.001), (15, 0)))]
    rows = _step_output(
        tmp_path, base, other,
        [{"type": "line_overlay", "source": "other", "fields": {"ds2": "name"}, "tolerance": 0.01}],
        "SELECT ds2, ST_AsText(ST_StartPoint(geom)), ST_AsText(ST_EndPoint(geom)), ST_NPoints(geom), "
        "ST_YMin(geom), ST_YMax(geom), round(ST_XMin(geom), 3) FROM t ORDER BY ST_XMin(geom)",
    )
    # Cut on the base line itself: y stays 0, the vertex at x=10 is kept, and each piece
    # starts exactly where the previous one ends (the cuts themselves may be off by up to
    # the tolerance, wherever the overlay line ends).
    assert [(r[0], r[6]) for r in rows] == [(None, 0), ("a", 2), ("b", 8), (None, 15)]
    assert all(r[4] == r[5] == 0 for r in rows)
    assert [r[3] for r in rows] == [2, 2, 3, 2]
    assert rows[0][1] == "POINT (0 0)" and rows[-1][2] == "POINT (20 0)"
    assert all(prev[2] == nxt[1] for prev, nxt in zip(rows, rows[1:]))


def test_line_overlay_source_ending_near_a_line_end_leaves_no_sliver(tmp_path):
    # The source line stops 3 cm short of one end of the path and overshoots the other.
    base = [("main", _line((0, 0), (10, 0)))]
    other = [("x", _line((0.03, 0.01), (10.02, 0)))]
    rows = _step_output(
        tmp_path, base, other,
        [{"type": "line_overlay", "source": "other", "fields": {"ds2": "name"}, "tolerance": 0.05}],
        "SELECT ds2, ST_AsText(geom) FROM t",
    )
    assert rows == [("x", "LINESTRING (0 0, 10 0)")]


def test_line_overlay_handles_3d_lines_and_keeps_their_z(tmp_path):
    # ST_LineLocatePoint used to crash the whole process (no Python error) on lines with Z,
    # which is what NVDB's road network exports are.
    base = [("main", _line((0, 0, 0), (10, 0, 0), (20, 0, 100)))]
    other = [("x", _line((5, 0, 7), (15, 0, 7)))]
    rows = _step_output(
        tmp_path, base, other,
        [{"type": "line_overlay", "source": "other", "fields": {"ds2": "name"}}],
        "SELECT ds2, ST_AsText(geom) FROM t ORDER BY ST_XMin(geom)",
    )
    # Cut 2D positions along the line, so the pieces keep the base line's own heights.
    assert rows == [
        (None, "LINESTRING Z (0 0 0, 5 0 0)"),
        ("x", "LINESTRING Z (5 0 0, 10 0 0, 15 0 50)"),
        (None, "LINESTRING Z (15 0 50, 20 0 100)"),
    ]


def test_force_2d_drops_z_on_load(tmp_path):
    import duckdb

    from duck_soup.derive import init_duckdb

    uri = _write_geojson(tmp_path / "base.geojson", [("main", _line((0, 0, 3), (10, 0, 5)))])

    def geom(**opts):
        cfg = load_config_dict({
            "name": "t", "working_crs": "EPSG:25833", "base": "base", "steps": [], "mapping": [],
            "sources": [{"id": "base", "format": "geojson", "uri": uri, "crs": "EPSG:25833", **opts}],
            "output": {"path": str(tmp_path / "unused.gpkg"), "layer": "out", "crs": "EPSG:25833"},
        })
        con = duckdb.connect()
        init_duckdb(con)
        Engine(cfg.pipelines[0])._prepare(con, tmp_path)
        return con.execute("SELECT ST_AsText(geom) FROM src_base").fetchone()[0]

    assert geom() == "LINESTRING Z (0 0 3, 10 0 5)"
    assert geom(force_2d=True) == "LINESTRING (0 0, 10 0)"


def _config(tmp_path: Path, base: list, steps: list[dict], mapping: list[dict], **pipeline):
    src = {"id": "base", "format": "geojson", "uri": _write_geojson(tmp_path / "base.geojson", base),
           "crs": pipeline.pop("base_crs", "EPSG:25833")}
    return load_config_dict({
        "name": "t", "sources": [src], "base": "base", "steps": steps, "mapping": mapping,
        "output": {"path": str(tmp_path / "out.gpkg"), "layer": "out", "crs": "EPSG:25833"},
        **pipeline,
    })


def test_dissolve_works_without_explicit_mapping(tmp_path):
    # The default "all columns" output excludes the internal __src_row column, which
    # dissolve used to drop: preview and run both failed with a binder error.
    base = [("a", _square(0, 0, 1, 1)), ("b", _square(1, 0, 2, 1))]
    cfg = _config(tmp_path, base, [{"type": "dissolve"}], [], working_crs="EPSG:25833")
    rows = preview_config_pipeline(cfg, limit=10)
    assert len(rows) == 1 and "__src_row" not in rows[0]
    assert _gpkg_layers(Path(run_config(cfg))) == {"out": 1}


@pytest.mark.parametrize("steps,mapping", [
    ([{"type": "buffer", "distance": 500}], []),
    ([], [{"to": "area", "func": "area"}]),
])
def test_metric_operations_refused_in_geographic_working_crs(tmp_path, steps, mapping):
    base = [("p", _point(10.75, 59.91))]
    cfg = _config(tmp_path, base, steps, mapping, base_crs="EPSG:4326")
    with pytest.raises(ValueError, match="in degrees.*EPSG:25833"):
        preview_config_pipeline(cfg, limit=10)

    # Fine once a projected working CRS is set explicitly...
    projected = _config(tmp_path, base, steps, mapping, base_crs="EPSG:4326", working_crs="EPSG:25833")
    assert len(preview_config_pipeline(projected, limit=10)) == 1
    # ...and a geographic one is fine when nothing measures distance or area.
    plain = _config(tmp_path, base, [], [{"to": "name", "from": "name"}], base_crs="EPSG:4326")
    assert len(preview_config_pipeline(plain, limit=10)) == 1


def test_lon_lat_use_centroid_taken_in_working_crs(tmp_path):
    # A long diagonal line: its centroid in EPSG:25833 and its centroid computed on lon/lat
    # degrees are ~108 km apart, so this tells the two apart.
    line = {"type": "LineString", "coordinates": [[100000, 6400000], [900000, 7900000]]}
    cfg = _config(
        tmp_path, [("diag", line)], [],
        [{"to": "lon", "func": "lon"}, {"to": "lat", "func": "lat"}], working_crs="EPSG:25833",
    )
    (row,) = preview_config_pipeline(cfg, limit=10)

    import duckdb

    from duck_soup.derive import init_duckdb

    con = duckdb.connect()
    init_duckdb(con)
    expected = con.execute(
        "SELECT round(ST_X(c), 7), round(ST_Y(c), 7) FROM (SELECT ST_Transform("
        "ST_Point(500000, 7150000), 'EPSG:25833', 'EPSG:4326', always_xy := true) AS c)"
    ).fetchone()
    assert (row["lon"], row["lat"]) == pytest.approx(expected)


def test_geometry_funcs_x_y_geohash_wkt_geom_type(tmp_path):
    cfg = _config(
        tmp_path, [("p", _point(262000.5, 6649000.25))], [],
        [{"to": f, "func": f} for f in ("x", "y", "geohash", "wkt", "geom_type")],
        working_crs="EPSG:25833",
    )
    (row,) = preview_config_pipeline(cfg, limit=10)
    assert (row["x"], row["y"]) == (262000.5, 6649000.25)  # working CRS, not lon/lat
    assert row["geohash"].startswith("u4xsu")  # central Oslo
    assert row["wkt"].startswith("POINT (10.7")  # EPSG:4326, lon first
    assert row["geom_type"] == "POINT"


def test_geohash_matches_reference():
    from duck_soup.derive import _to_geohash

    assert _to_geohash(10.40744, 57.64911) == "u4pruydqqvj8"  # Wikipedia's example, + 1 char
    assert _to_geohash(None, 1.0) is None and _to_geohash(200.0, 0.0) is None


def test_seq_numbers_rows_in_preview_and_run(tmp_path):
    base = [(n, _point(i * 10, 0)) for i, n in enumerate("abc")]
    cfg = _config(tmp_path, base, [], [{"to": "name", "from": "name"}, {"to": "n", "func": "seq"}])
    assert sorted(r["n"] for r in preview_config_pipeline(cfg, limit=10)) == [1, 2, 3]
    out = run_config(cfg)
    with sqlite3.connect(out) as con:
        assert sorted(n for (n,) in con.execute('SELECT n FROM "out"')) == [1, 2, 3]


def test_seq_works_without_geometry_but_x_does_not(tmp_path):
    csv = tmp_path / "plain.csv"
    csv.write_text("name\na\nb\n", encoding="utf-8")

    def cfg(func):
        return load_config_dict({
            "name": "t", "sources": [{"id": "base", "format": "csv", "uri": str(csv)}], "base": "base",
            "mapping": [{"to": "v", "func": func}],
            "output": {"path": str(tmp_path / "out.gpkg"), "layer": "out"},
        })

    assert sorted(r["v"] for r in preview_config_pipeline(cfg("seq"), limit=10)) == [1, 2]
    with pytest.raises(ValueError, match="has no geometry"):
        preview_config_pipeline(cfg("x"), limit=10)


def test_preview_of_branch_step_shows_that_branch(tmp_path):
    # A step on a snapshot branch (here: buffer the snapshot) leaves the main chain
    # untouched; previewing that step has to show the branch, or edits to it never
    # visibly change the preview.
    import json

    cfg = _config(
        tmp_path, [("p", _point(0, 0))],
        [{"type": "snapshot", "id": "snap"}, {"type": "buffer", "branch": "snap", "distance": 10}],
        [], working_crs="EPSG:25833",
    )

    def geom_types(**kw):
        return {json.loads(r["__geojson"])["type"] for r in preview_config_pipeline(cfg, limit=10, **kw)}

    assert geom_types(preview_until_step=2) == {"Polygon"}  # the buffered branch
    assert geom_types(preview_until_step=1) == {"Point"}  # the snapshot itself
    assert geom_types() == {"Point"}  # the full pipeline output is still the main chain


def test_run_without_mapping_writes_every_column_from_geojson(tmp_path):
    # ST_Read adds GDAL's OGC_FID to GeoJSON rows; with no mapping it used to be passed
    # through to the GeoPackage writer, which rejects a field by that name.
    src = {"id": "base", "format": "geojson", "uri": _write_geojson(tmp_path / "base.geojson", _POINTS),
           "crs": "EPSG:25833"}
    cfg = load_config_dict({
        "name": "t", "output": str(tmp_path / "out.gpkg"),
        "pipelines": [{"name": "t", "sources": [src], "base": "base", "mapping": [],
                       "layers": [{"layer": "out", "crs": "EPSG:25833"}]}],
    })

    out_path = run_config(cfg)

    assert _gpkg_layers(Path(out_path)) == {"out": 4}
    con = sqlite3.connect(out_path)
    try:
        cols = {r[1].lower() for r in con.execute('PRAGMA table_info("out")').fetchall()}
    finally:
        con.close()
    assert "name" in cols
    assert not cols & {"ogc_fid", "__src_row", "__geom4326", "__centroid4326"}


def test_length_func_measures_lines_and_polygon_perimeters(tmp_path):
    # func: length used plain ST_Length, which is 0 for polygons.
    base = [("square", _square(0, 0, 10, 10)),
            ("line", {"type": "LineString", "coordinates": [[0, 0], [3, 4]]})]
    cfg = _config(tmp_path, base, [], [{"to": "name", "from": "name"}, {"to": "len", "func": "length"}],
                  working_crs="EPSG:25833")
    rows = {r["name"]: r["len"] for r in preview_config_pipeline(cfg, limit=10)}
    assert rows == {"square": pytest.approx(40), "line": pytest.approx(5)}


def test_tutorial_config_runs(tmp_path, monkeypatch):
    # The packaged tutorial (duck_soup/tutorial/, generated by scripts/build_tutorial.py) holds
    # every example from the docs; an engine change that breaks one should fail here, not in the
    # docs. Installed exactly as `duck-soup tutorial` does, and run from that folder.
    from duck_soup.tutorial import install

    install(tmp_path)
    monkeypatch.chdir(tmp_path)
    cfg = load_config(tmp_path / "pipelines" / "pondsworth.yaml")
    cfg.output = str(tmp_path / "tutorial.gpkg")

    layers = _gpkg_layers(Path(run_config(cfg)))

    rejects = {name for p in cfg.pipelines for name in reject_layer_names(p.steps)}
    assert rejects == {"second_in_category", "unplaced_sightings"}
    assert set(layers) == {p.name for p in cfg.pipelines} | rejects
    assert layers["second_in_category"] == 4 and layers["unplaced_sightings"] == 1
    assert layers["spatial_join"] == 11
    assert layers["dissolve"] == 2
    assert layers["spatial_join_all"] == 7


# GDAL's GeoPackage writer crashes the whole process on these column types (see
# output.gpkg_safe_select), so they're cast to what a GeoPackage stores before writing.
_GPKG_UNSAFE_EXPRS = {
    "total": "sum(1::INTEGER) OVER ()",  # HUGEINT
    "unsigned": "1::UHUGEINT",
    "amount": "sum(1.25::DECIMAL(10, 2)) OVER ()",  # DECIMAL(38, 2)
    "counts": "[1::HUGEINT, 2::HUGEINT]",
    "info": "{'n': 3::HUGEINT, 'x': 1.5::DECIMAL(20, 1)}",
    "narrow": "1.25::DECIMAL(18, 2)",  # fine as it is, left alone
}


def _gpkg_types_config(tmp_path, mapping):
    point = {"type": "Point", "coordinates": [10.75, 59.91]}
    uri = _write_geojson(tmp_path / "pts.geojson", [("a", point)])
    return load_config_dict({
        "name": "t", "working_crs": "EPSG:25833", "base": "pts", "steps": [],
        "sources": [{"id": "pts", "format": "geojson", "uri": uri, "crs": "EPSG:4326"}],
        "mapping": mapping,
        "output": {"path": str(tmp_path / "out.gpkg"), "layer": "out", "crs": "EPSG:25833"},
    })


def test_gpkg_output_casts_types_the_writer_cannot_take(tmp_path):
    import duckdb

    from duck_soup.derive import init_duckdb

    mapping = [{"to": to, "expr": expr} for to, expr in _GPKG_UNSAFE_EXPRS.items()]
    log = []
    out = run_config(_gpkg_types_config(tmp_path, mapping), log=log.append)
    con = duckdb.connect()
    init_duckdb(con)
    rel = con.execute(f"SELECT * EXCLUDE (geom) FROM ST_Read('{out}')")
    row = dict(zip([d[0] for d in rel.description], rel.fetchone()))
    assert (row["total"], row["unsigned"], row["amount"], row["narrow"]) == (1, 1, 1.25, 1.25)
    assert "1" in str(row["counts"]) and "2" in str(row["counts"])
    assert (row["info.n"], row["info.x"]) == (3, 1.5)  # GDAL flattens a struct
    assert any("'total': HUGEINT written as BIGINT" in line for line in log)
    assert not any("'narrow'" in line for line in log)


def test_gpkg_output_names_a_column_too_large_for_64_bits(tmp_path):
    cfg = _gpkg_types_config(tmp_path, [{"to": "big", "expr": "(2::HUGEINT ** 70)::HUGEINT"}])
    with pytest.raises(Exception, match="column 'big' of layer 'out' has a value too large"):
        run_config(cfg)


def _mapping_error(tmp_path, mapping, run=False):
    from duck_soup.engine import MappingError

    point = {"type": "Point", "coordinates": [10.75, 59.91]}
    uri = _write_geojson(tmp_path / "pts.geojson", [("a", point)])
    cfg = load_config_dict({
        "name": "t", "working_crs": "EPSG:25833", "base": "pts", "steps": [],
        "sources": [{"id": "pts", "format": "geojson", "uri": uri, "crs": "EPSG:4326"}],
        "mapping": mapping,
        "output": {"path": str(tmp_path / "out.gpkg"), "layer": "out", "crs": "EPSG:25833"},
    })
    with pytest.raises(MappingError) as info:
        run_config(cfg) if run else preview_config_pipeline(cfg, limit=5)
    return str(info.value).split("\n")


def test_mapping_errors_name_the_row_and_its_path(tmp_path):
    ok = [{"to": "name", "from": "name"}, {"to": "upper", "expr": "SELECT upper(name)"}]

    head, path, detail = _mapping_error(tmp_path, ok + [{"to": "w", "expr": "least(1, 2"}])
    assert head.startswith("mapping 'w' (expr): Parser Error: syntax error at end of input")
    assert path == "pipelines.0.mapping.2"  # the shape the Problems tab links to the row

    head, path, _ = _mapping_error(tmp_path, ok + [{"to": "c", "from": "nmae"}])
    assert head.startswith("mapping 'c' (from): Binder Error") and '"nmae"' in head
    assert path == "pipelines.0.mapping.2"  # not the valid `SELECT upper(name)` before it


def test_run_reports_a_mapping_error_before_writing(tmp_path):
    head, path, _ = _mapping_error(tmp_path, [{"to": "x", "expr": "nope + 1"}], run=True)
    assert head.startswith("mapping 'x' (expr): Binder Error") and path == "pipelines.0.mapping.0"
    assert not (tmp_path / "out.gpkg").exists()


# ---- attributes step / dissolve keys and aggregates -------------------------

def test_attributes_step_sets_columns_in_order_and_drops(tmp_path):
    base = [("oslo-1", _point(262000, 6649000)), ("oslo-2", _point(262100, 6649000))]
    steps = [{"type": "attributes", "columns": [
        {"to": "city", "expr": "split_part(name, '-', 1)"},
        {"to": "key", "expr": "upper(city)"},  # uses the column set just before it
        {"to": "n", "expr": "split_part(name, '-', 2)", "cast": "INTEGER"},
        {"to": "kind", "codelist": {"source": "city", "cases": [{"match": "oslo", "value": "capital"}]}},
        {"to": "lon", "func": "lon"},
        {"to": "label", "from": "name"},
        {"to": "name", "expr": "name || '!'"},  # overwrites in place
    ], "drop": ["label"]}]
    cfg = _config(tmp_path, base, steps, [], working_crs="EPSG:25833")
    rows = sorted(preview_config_pipeline(cfg, limit=10), key=lambda r: r["n"])
    assert [(r["name"], r["city"], r["key"], r["n"], r["kind"]) for r in rows] == [
        ("oslo-1!", "oslo", "OSLO", 1, "capital"), ("oslo-2!", "oslo", "OSLO", 2, "capital"),
    ]
    assert rows[0]["lon"] == pytest.approx(10.7, abs=0.1)
    assert not {"label", "__keep", "__geom4326", "__centroid4326"} & set(rows[0])


def test_attributes_step_feeds_later_steps(tmp_path):
    base = [("a", _point(0, 0)), ("b", _point(10, 0)), ("c", _point(20, 0))]
    steps = [
        {"type": "attributes", "columns": [{"to": "keep", "expr": "name <> 'b'"}]},
        {"type": "filter", "where": "keep"},
    ]
    cfg = _config(tmp_path, base, steps, [{"to": "name", "from": "name"}], working_crs="EPSG:25833")
    assert sorted(r["name"] for r in preview_config_pipeline(cfg, limit=10)) == ["a", "c"]
    with sqlite3.connect(run_config(cfg)) as con:
        assert sorted(n for (n,) in con.execute('SELECT name FROM "out"')) == ["a", "c"]


def test_attributes_step_replaces_the_only_column_of_a_table():
    import duckdb

    from duck_soup.config import Attributes
    from duck_soup.derive import init_duckdb
    from duck_soup.steps import StepContext, attributes_sql

    con = duckdb.connect()
    init_duckdb(con)
    con.execute("CREATE VIEW one_col AS SELECT 'a' AS name")
    step = Attributes(columns=[{"to": "name", "expr": "upper(name)"}])
    sql = attributes_sql("one_col", step, lambda m: None, StepContext("EPSG:25833", False, "b"))
    assert con.execute(sql).fetchall() == [("A",)]


def test_attributes_step_refuses_geometry_funcs_and_measures_in_degrees(tmp_path):
    csv = tmp_path / "plain.csv"
    csv.write_text("name\na\n", encoding="utf-8")
    cfg = load_config_dict({
        "name": "t", "sources": [{"id": "base", "format": "csv", "uri": str(csv)}], "base": "base",
        "steps": [{"type": "attributes", "columns": [{"to": "x", "func": "x"}]}],
        "mapping": [], "output": {"path": str(tmp_path / "out.gpkg"), "layer": "out"},
    })
    with pytest.raises(ValueError, match="has no geometry"):
        preview_config_pipeline(cfg, limit=10)

    area = [{"type": "attributes", "columns": [{"to": "a", "func": "area"}]}]
    geographic = _config(tmp_path, [("p", _point(10.75, 59.91))], area, [], base_crs="EPSG:4326")
    with pytest.raises(ValueError, match="in degrees"):
        preview_config_pipeline(geographic, limit=10)


def test_attributes_step_cannot_drop_geom(tmp_path):
    with pytest.raises(ValueError, match="'geom' can't be dropped"):
        _config(tmp_path, [("p", _point(0, 0))], [{"type": "attributes", "drop": ["geom"]}], [])


def test_dissolve_by_expression_with_aggregates(tmp_path):
    base = [("a-1", _square(0, 0, 1, 1)), ("a-22", _square(1, 0, 2, 1)), ("b-1", _square(5, 0, 6, 1))]
    steps = [{"type": "dissolve", "by": [{"to": "group", "expr": "split_part(name, '-', 1)"}], "aggregates": [
        {"to": "n", "expr": "count(*)"},
        {"to": "total", "expr": "sum(length(name))"},
        {"to": "names", "expr": "string_agg(name, ',' ORDER BY name)"},
        {"to": "area", "expr": "sum(ST_Area(geom))"},
    ]}]
    cfg = _config(tmp_path, base, steps, [], working_crs="EPSG:25833")
    rows = sorted(preview_config_pipeline(cfg, limit=10), key=lambda r: r["group"])
    assert [(r["group"], r["n"], r["total"], r["names"], r["area"]) for r in rows] == [
        ("a", 2, 7, "a-1,a-22", 2.0), ("b", 1, 3, "b-1", 1.0),
    ]
    assert _gpkg_layers(Path(run_config(cfg))) == {"out": 2}


def test_dissolve_mixes_column_and_expression_keys_and_aggregates_without_keys(tmp_path):
    base = [("a", _square(0, 0, 1, 1)), ("a", _square(1, 0, 2, 1)), ("b", _square(5, 0, 6, 1))]
    keyed = [{"type": "dissolve", "by": ["name", {"to": "big", "expr": "upper(name)"}]}]
    rows = preview_config_pipeline(_config(tmp_path, base, keyed, [], working_crs="EPSG:25833"), limit=10)
    assert sorted((r["name"], r["big"]) for r in rows) == [("a", "A"), ("b", "B")]

    everything = [{"type": "dissolve", "aggregates": [{"to": "n", "expr": "count(*)"}]}]
    (row,) = preview_config_pipeline(_config(tmp_path, base, everything, [], working_crs="EPSG:25833"), limit=10)
    assert row["n"] == 3


def test_dissolve_output_names_must_be_unique(tmp_path):
    steps = [{"type": "dissolve", "by": ["name"], "aggregates": [{"to": "NAME", "expr": "count(*)"}]}]
    with pytest.raises(ValueError, match="column 'NAME' is produced twice"):
        _config(tmp_path, [("p", _point(0, 0))], steps, [])


def _chains(tmp_path, base, step):
    cfg = _config(tmp_path, base, [{"type": "combine_lines", **step}], [], working_crs="EPSG:25833")
    return [(r["name"], r.get("ids"), r.get("len")) for r in preview_config_pipeline(cfg, limit=50)]


def test_combine_lines_joins_each_connected_chain_with_its_own_aggregates(tmp_path):
    base = [
        ("a", _line((0, 0), (10, 0))), ("a", _line((10, 0), (20, 0))), ("a", _line((20, 0), (30, 0))),
        ("a", _line((100, 0), (110, 0))),  # same name, not connected: a chain of its own
        ("b", _line((30, 0), (40, 0))),  # connected, but another name
    ]
    aggs = [{"to": "ids", "expr": "count(*)"}, {"to": "len", "expr": "sum(ST_Length(geom))"}]
    assert sorted(_chains(tmp_path, base, {"by": ["name"], "aggregates": aggs})) == [
        ("a", 1, 10.0), ("a", 3, 30.0), ("b", 1, 10.0),
    ]
    cfg = _config(tmp_path, base, [{"type": "combine_lines", "by": ["name"]}], [], working_crs="EPSG:25833")
    assert _gpkg_layers(Path(run_config(cfg))) == {"out": 3}


def test_combine_lines_stops_at_junctions_and_keeps_direction_unless_told(tmp_path):
    aggs = [{"to": "name", "expr": "min(name)"}, {"to": "ids", "expr": "string_agg(name, ',' ORDER BY name)"}]
    # c starts where a and b meet: a chain stops where three lines meet
    junction = [("a", _line((0, 0), (10, 0))), ("b", _line((10, 0), (20, 0))), ("c", _line((10, 0), (10, 10)))]
    assert sorted(ids for _, ids, _ in _chains(tmp_path, junction, {"aggregates": aggs})) == ["a", "b", "c"]
    # b runs the other way
    opposed = [("a", _line((0, 0), (10, 0))), ("b", _line((20, 0), (10, 0)))]
    assert sorted(ids for _, ids, _ in _chains(tmp_path, opposed, {"aggregates": aggs})) == ["a", "b"]
    assert _chains(tmp_path, opposed, {"aggregates": aggs, "preserve_direction": False}) == [("a", "a,b", None)]


def test_combine_lines_keeps_features_that_arent_lines(tmp_path):
    base = [("a", _line((0, 0), (10, 0))), ("a", _line((10, 0), (20, 0))), ("p", _point(5, 5)), ("s", _square(0, 0, 1, 1))]
    rows = _chains(tmp_path, base, {"by": ["name"], "aggregates": [{"to": "ids", "expr": "count(*)"}]})
    assert rows == [("a", 2, None), ("p", 1, None), ("s", 1, None)]


def test_combine_lines_errors_name_the_aggregate(tmp_path):
    head, path, _ = _step_error(tmp_path, [{"type": "combine_lines", "aggregates": [
        {"to": "bad", "expr": "ST_Length(geom)"},  # not aggregated
    ]}])
    assert head.startswith("step 1 aggregate 'bad' (expr): Binder Error")
    assert path == "pipelines.0.steps.0.aggregates.0"


def _roads(tmp_path, step: dict) -> list[dict]:
    """`step` (a dissolve or combine_lines) over four road segments with a few attributes:
    a, b and c join end to end on Main St (b is the longest), d is a side road."""
    import json

    feats = [
        ("a", "Main St", 30, "A", True, [(0, 0), (10, 0)]),
        ("b", "Main St", 50, "B", False, [(10, 0), (40, 0)]),
        ("c", "Main St", 30, "A", None, [(40, 0), (50, 0)]),
        ("d", "Side Rd", 20, None, True, [(100, 0), (105, 0)]),
    ]
    path = tmp_path / "roads.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": _line(*coords),
         "properties": {"id": i, "street": street, "speed": speed, "class": cls, "lit": lit}}
        for i, street, speed, cls, lit, coords in feats
    ]}), encoding="utf-8")
    cfg = load_config_dict({
        "name": "t", "working_crs": "EPSG:25833", "base": "roads",
        "sources": [{"id": "roads", "format": "geojson", "uri": str(path), "crs": "EPSG:25833"}],
        "steps": [step], "mapping": [],
        "output": {"path": str(tmp_path / "out.gpkg"), "layer": "out", "crs": "EPSG:25833"},
    })
    return sorted(preview_config_pipeline(cfg, limit=50), key=lambda r: r["street"])


@pytest.mark.parametrize("kind", ["dissolve", "combine_lines"])
def test_aggregates_without_sql(tmp_path, kind):
    (main, side) = _roads(tmp_path, {"type": kind, "by": ["street"], "aggregates": [
        {"from": "class", "how": "most_common"},
        {"to": "class_of_longest", "from": "class", "how": "from_largest"},
        {"to": "ids", "from": "id", "how": "all"},
        {"to": "total_speed", "from": "speed", "how": "sum"},
        {"to": "mean_speed", "from": "speed", "how": "average"},
        {"to": "slowest", "from": "speed", "how": "min"},
        {"to": "fastest", "from": "speed", "how": "max"},
        {"to": "segments", "how": "count"},
        {"to": "lit_count", "from": "lit", "how": "count"},  # counts the values, not NULLs
        {"to": "one", "from": "street", "how": "any"},
    ]})
    assert {k: main[k] for k in main if k not in ("__geojson", "street")} == {
        "class": "A", "class_of_longest": "B", "ids": "a, b, c", "total_speed": 110,
        "mean_speed": pytest.approx(110 / 3), "slowest": 30, "fastest": 50,
        "segments": 3, "lit_count": 2, "one": "Main St",
    }
    assert (side["ids"], side["class"], side["segments"]) == ("d", None, 1)


def test_aggregate_to_defaults_to_its_column_and_needs_a_complete_form(tmp_path):
    from duck_soup.config import Aggregate

    assert Aggregate.model_validate({"from": "speed", "how": "max"}).to == "speed"
    assert Aggregate.model_validate({"to": "n", "how": "count"}).from_ is None
    for bad, msg in [
        ({"from": "speed"}, "needs 'how'"),
        ({"to": "x", "how": "sum"}, "needs a column"),
        ({"how": "count"}, "needs a name"),
        ({"to": "x", "from": "speed", "how": "sum", "expr": "sum(speed)"}, "not both"),
        ({"from": "speed", "how": "median"}, "how"),
    ]:
        with pytest.raises(ValueError, match=msg):
            Aggregate.model_validate(bad)


def test_grouped_steps_dump_type_first_and_aggregates_without_their_default_name(tmp_path):
    import yaml

    from duck_soup.config import dump_config_yaml

    cfg = _config(tmp_path, [("a", _line((0, 0), (1, 0)))], [{"type": "combine_lines", "aggregates": [
        {"from": "name", "how": "any"}, {"to": "first", "from": "name", "how": "min"},
    ]}], [])
    step = yaml.safe_load(dump_config_yaml(cfg))["pipelines"][0]["steps"][0]
    assert list(step)[0] == "type"
    assert step["aggregates"] == [{"from": "name", "how": "any"}, {"to": "first", "from": "name", "how": "min"}]


def test_preview_sample_runs_the_chain_on_the_first_base_features(tmp_path):
    base = [(f"r{i}", _line((i * 10, 0), (i * 10 + 10, 0))) for i in range(6)]  # one chain of 6
    cfg = _config(tmp_path, base, [{"type": "combine_lines", "aggregates": [{"to": "n", "how": "count"}]}], [])
    assert [r["n"] for r in preview_config_pipeline(cfg, limit=50)] == [6]
    assert [r["n"] for r in preview_config_pipeline(cfg, limit=50, sample=4)] == [4]


def test_aggregate_sql_covers_every_how():
    from duck_soup.config import AGGREGATE_HOWS
    from duck_soup.steps import AGGREGATE_SQL

    assert list(AGGREGATE_SQL) == AGGREGATE_HOWS


@pytest.mark.parametrize("agg,message", [
    ({"to": "s", "from": "class", "how": "sum"}, "class is text, and sum only works on numbers"),
    ({"to": "s", "from": "lit", "how": "average"}, "lit is true/false, and average only works"),
    ({"to": "s", "from": "colour", "how": "any"}, "there's no column 'colour' at this step"),
])
def test_aggregates_without_sql_explain_a_column_that_cant_work(tmp_path, agg, message):
    from duck_soup.engine import MappingError

    with pytest.raises(MappingError) as info:
        _roads(tmp_path, {"type": "combine_lines", "by": ["street"], "aggregates": [
            {"from": "speed", "how": "max"}, agg,
        ]})
    head, path, _ = str(info.value).split("\n")
    assert head.startswith(f"step 1 aggregate 's' ({agg['how']}): {message}")
    assert path == "pipelines.0.steps.0.aggregates.1"


def _step_error(tmp_path, steps):
    from duck_soup.engine import MappingError

    base = [("a", _square(0, 0, 1, 1))]
    cfg = _config(tmp_path, base, steps, [], working_crs="EPSG:25833")
    with pytest.raises(MappingError) as info:
        preview_config_pipeline(cfg, limit=5)
    return str(info.value).split("\n")


def test_step_column_errors_name_the_column_and_its_path(tmp_path):
    buffer = {"type": "buffer", "distance": 1}
    good = {"to": "up", "expr": "upper(name)"}

    head, path, _ = _step_error(tmp_path, [buffer, {"type": "attributes", "columns": [
        good, {"to": "w", "expr": "least(1, 2"},
    ]}])
    assert head.startswith("step 2 column 'w' (expr): Parser Error: syntax error at end of input")
    assert path == "pipelines.0.steps.1.columns.1"

    head, path, _ = _step_error(tmp_path, [{"type": "attributes", "columns": [
        good, {"to": "c", "from": "nmae"},
    ]}])
    assert head.startswith("step 1 column 'c' (from): Binder Error") and path == "pipelines.0.steps.0.columns.1"


def test_dissolve_errors_name_the_key_or_aggregate(tmp_path):
    head, path, _ = _step_error(tmp_path, [{"type": "dissolve", "by": ["name", "nmae"]}])
    assert head.startswith("step 1 group key 'nmae' (column): Binder Error")
    assert path == "pipelines.0.steps.0.by.1"

    head, path, _ = _step_error(tmp_path, [{"type": "dissolve", "by": ["name"], "aggregates": [
        {"to": "n", "expr": "count(*)"}, {"to": "bad", "expr": "ST_Area(geom)"},  # not aggregated
    ]}])
    assert head.startswith("step 1 aggregate 'bad' (expr): Binder Error")
    assert path == "pipelines.0.steps.0.aggregates.1"


def test_attribute_and_dissolve_steps_in_plan_counts_branches_and_geoparquet(tmp_path):
    import duckdb

    from duck_soup.derive import init_duckdb
    from duck_soup.engine import plan_config_pipeline

    base = [("a-1", _square(0, 0, 1, 1)), ("a-2", _square(1, 0, 2, 1)), ("b-1", _square(5, 0, 6, 1))]
    steps = [
        {"type": "attributes", "columns": [{"to": "grp", "expr": "split_part(name, '-', 1)"}]},
        {"type": "snapshot", "id": "copy"},
        # on the branch only: the main chain keeps its three rows and their names
        {"type": "attributes", "branch": "copy", "drop": ["name"]},
        {"type": "dissolve", "branch": "copy", "by": ["grp"], "aggregates": [{"to": "n", "expr": "count(*)"}]},
        # `left` names a column the join source has too: it must still mean the running row's
        # (it used to resolve to the source's own, so every row matched the first group)
        {"type": "attribute_join", "source": "copy", "left": "grp", "right": "grp", "fields": {"n": "n"}},
    ]
    mapping = [{"to": "name", "from": "name"}, {"to": "n", "from": "n"}]
    cfg = _config(tmp_path, base, steps, mapping, working_crs="EPSG:25833")

    rows = sorted((r["name"], r["n"]) for r in preview_config_pipeline(cfg, limit=10))
    assert rows == [("a-1", 2), ("a-2", 2), ("b-1", 1)]

    # The SQL tab shows the new steps without reading anything.
    sql = "\n".join(e["sql"] for e in plan_config_pipeline(cfg))
    assert "split_part(name, '-', 1)" in sql and "count(*)" in sql and "GROUP BY 1" in sql

    engine = Engine(cfg.pipelines[0])
    counts = engine.counts()
    assert counts["steps"] == {1: 3, 2: 3, 3: 3, 4: 2, 5: 3} and counts["layers"] == [3]

    cfg.output = str(tmp_path / "out.parquet")
    out = run_config(cfg)
    con = duckdb.connect()
    init_duckdb(con)
    assert sorted(con.execute(f"SELECT name, n FROM read_parquet('{out}')").fetchall()) == rows
