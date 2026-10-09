"""End-to-end pipeline execution against the checked-in fixture YAMLs +
data/ files, asserting on the GeoPackage(s) actually written to disk.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from duck_soup.config import CodeCase, CodeList, load_config, load_config_dict
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
    condition = Engine._case_condition('"category"', CodeCase(is_blank=True, value="Unknown"), True)
    assert condition == '("category" IS NULL OR "category" = \'\')'


def test_codelist_expr_routes_blank_and_specific_value_to_same_output():
    cfg = load_config(PIPELINES_DIR / "test.yaml")
    engine = Engine(cfg.pipelines[0].to_pipeline(cfg.output))
    cl = CodeList(
        source="category",
        cases=[
            CodeCase(match="n/a", value="Unknown"),
            CodeCase(is_blank=True, value="Unknown"),
        ],
    )
    expr = engine._codelist_expr(cl)
    assert "IS NULL" in expr
    assert expr.count("THEN 'Unknown'") == 2


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
    engine = Engine(cfg.pipelines[0].to_pipeline(cfg.output))
    con = duckdb.connect()
    init_duckdb(con)
    view = engine._prepare(con, tmp_path)
    return con.execute(sql.replace(" t", f' "{view}"')).fetchall()


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
        Engine(cfg.pipelines[0].to_pipeline(cfg.output))._prepare(con, tmp_path)
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

    assert set(layers) == {p.name for p in cfg.pipelines}
    assert layers["spatial_join"] == 11
    assert layers["dissolve"] == 2
    assert layers["spatial_join_all"] == 7
