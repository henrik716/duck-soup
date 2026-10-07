"""Build the tutorial sample data, run every tutorial example, and generate the docs assets.

    python scripts/build_tutorial.py

Writes:
  data/tutorial/*.geojson, categories.csv   the fictional "Pondsworth" sample data
  data/tutorial/pondsworth.yaml             one pipeline per tutorial example (runnable config)
  docs/tutorials/generated/<name>.yaml      the YAML shown for each example
  docs/tutorials/generated/<name>.md        result tables (real engine output)
  docs/tutorials/generated/<name>.svg       before/after diagrams (real engine output)

The tutorial pages include these with pymdownx.snippets, so re-running this script after an
engine change keeps every table and diagram in the docs truthful. Run it from the repo root.

Pondsworth is laid out in a local metre grid (x 0–1000, y 0–800) and placed in UTM zone 31N
(EPSG:32631) somewhere in the North Sea, so it can't be mistaken for a real town. Sources are
written as GeoJSON in EPSG:4326 like most real data; the pipelines work in EPSG:32631.
"""
from __future__ import annotations

import csv
import json
import re
import tempfile
from pathlib import Path

import duckdb
import yaml

from duck_soup.config import load_config_dict
from duck_soup.engine import run_config

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "tutorial"
GEN = ROOT / "docs" / "tutorials" / "generated"
DATA_REL = "data/tutorial"
UTM = "EPSG:32631"
X0, Y0 = 500_000, 5_700_000

# ---------------------------------------------------------------------------
# Sample data (local metre coordinates)
# ---------------------------------------------------------------------------

DISTRICTS = [
    ("Old Puddleton", "Upstream", 12000, [(0, 400), (500, 400), (500, 800), (0, 800)]),
    ("Mallard Quay", "Upstream", 8500, [(500, 400), (1000, 400), (1000, 800), (500, 800)]),
    ("Featherhill", "Downstream", 6200, [(0, 0), (500, 0), (500, 400), (0, 400)]),
    ("Reedmoor", "Downstream", 4300, [(500, 0), (1000, 0), (1000, 400), (500, 400)]),
]

# A 60 m wide river running from west-northwest to east-southeast across town.
RIVER = [(-50, 511), (1050, 269), (1050, 329), (-50, 571)]

PARKS = [
    ("Central Puddle Park", "park", [(420, 330), (640, 330), (640, 520), (420, 520)]),
    ("Willow Wallow", "park", [(100, 100), (260, 100), (260, 240), (100, 240)]),
    ("Kingfisher Meadow", "meadow", [(700, 550), (900, 550), (900, 720), (700, 720)]),
    ("Bulrush Fields", "meadow", [(760, 60), (960, 60), (960, 240), (760, 240)]),
]

# name, category code, opened (text, as found in the wild), bread_crumbs (per year), inspected (mixed formats), x, y
PLACES = [
    ("Mallard Museum", "MUS", "1887", 52000, "2024-03-14", 200, 650),
    ("Puddle Lane Café", "CAF", "2015", 18000, "14.03.2024", 320, 700),
    ("Quackington Library", "LIB", "1962", 75000, "2023-11-02", 420, 610),
    ("Soggy Crust Café", "CAF", "1999", 9000, None, 640, 690),
    ("Duckling Swim School", "SCH", "1975", None, "March 2024", 820, 640),
    ("Breadcrumb Hill", "PRK", "unknown", 30000, None, 150, 180),
    ("Featherhill Library", "LIB", "2008", 21000, "2024-01-20", 330, 120),
    ("Museum of Rubber Ducks", "MUS", "2021", 14000, "2022-09-30", 720, 150),
    ("Reedmoor Pond Café", "CAF", "2010", 6000, None, 880, 200),
    ("Old Duck House", "", "1901", None, None, 450, 250),
    ("Old Lighthouse", "LND", "1820", 4000, None, 1250, 520),
]

# name, type code, x, y
STOPS = [
    ("Crumb Market", "T", 260, 560),
    ("Waddle Bridge", "B", 520, 440),
    ("Webfoot Lane", "B", 700, 600),
    ("Nest Gate", "T", 450, 760),
    ("Feather Road", "B", 250, 300),
    ("Reed Loop", "B", 650, 250),
    ("Paddle Boat Pier", "P", 980, 330),
]

NEW_PLACES = [
    ("Harbour Fish Market", "MKT", 950, 600),
    ("Pop-up Duck Gallery", "MUS", 560, 120),
]

CATEGORIES = [
    ("MUS", "Museum"),
    ("CAF", "Café"),
    ("LIB", "Library"),
    ("SCH", "School"),
    ("PRK", "Park"),
    ("MKT", "Market"),
]


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("LOAD spatial")
    return con


def _to_lonlat(con, x: float, y: float) -> list[float]:
    lon, lat = con.execute(
        f"SELECT ST_X(g), ST_Y(g) FROM (SELECT ST_Transform(ST_Point(?, ?), '{UTM}', 'EPSG:4326', always_xy := true) AS g)",
        [X0 + x, Y0 + y],
    ).fetchone()
    return [round(lon, 7), round(lat, 7)]


def _feature_collection(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def _polygon(con, ring):
    coords = [_to_lonlat(con, x, y) for x, y in ring]
    return {"type": "Polygon", "coordinates": [coords + [coords[0]]]}


def _point(con, x, y):
    return {"type": "Point", "coordinates": _to_lonlat(con, x, y)}


def write_data() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    con = _con()

    def dump(name, feats):
        (DATA / f"{name}.geojson").write_text(
            json.dumps(_feature_collection(feats), ensure_ascii=False, indent=1), encoding="utf-8"
        )

    dump("districts", [
        {"type": "Feature", "properties": {"district_name": n, "ward": w, "population": p}, "geometry": _polygon(con, r)}
        for n, w, p, r in DISTRICTS
    ])
    dump("river", [{"type": "Feature", "properties": {"name": "River Waddle"}, "geometry": _polygon(con, RIVER)}])
    dump("parks", [
        {"type": "Feature", "properties": {"name": n, "kind": k}, "geometry": _polygon(con, r)}
        for n, k, r in PARKS
    ])
    dump("places", [
        {"type": "Feature",
         "properties": {"name": n, "category": c, "opened": o, "bread_crumbs": v, "inspected": i},
         "geometry": _point(con, x, y)}
        for n, c, o, v, i, x, y in PLACES
    ])
    dump("stops", [
        {"type": "Feature", "properties": {"stop_name": n, "stop_type": t}, "geometry": _point(con, x, y)}
        for n, t, x, y in STOPS
    ])
    dump("new_places", [
        {"type": "Feature", "properties": {"name": n, "category": c}, "geometry": _point(con, x, y)}
        for n, c, x, y in NEW_PLACES
    ])
    with open(DATA / "categories.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["code", "label"])
        w.writerows(CATEGORIES)


SOURCES = [
    {"id": "places", "format": "geojson", "uri": f"{DATA_REL}/places.geojson", "crs": "EPSG:4326"},
    {"id": "districts", "format": "geojson", "uri": f"{DATA_REL}/districts.geojson", "crs": "EPSG:4326"},
    {"id": "parks", "format": "geojson", "uri": f"{DATA_REL}/parks.geojson", "crs": "EPSG:4326"},
    {"id": "river", "format": "geojson", "uri": f"{DATA_REL}/river.geojson", "crs": "EPSG:4326"},
    {"id": "stops", "format": "geojson", "uri": f"{DATA_REL}/stops.geojson", "crs": "EPSG:4326"},
    {"id": "new_places", "format": "geojson", "uri": f"{DATA_REL}/new_places.geojson", "crs": "EPSG:4326"},
    {"id": "categories", "format": "csv", "uri": f"{DATA_REL}/categories.csv"},
]

# ---------------------------------------------------------------------------
# Examples. Each one becomes a pipeline (and output layer) in pondsworth.yaml.
#   pipeline: the parts of the pipeline shown in the docs (base/steps/derived_sources/mapping)
#   table:    (header, SQL over the output layer `out`) columns, plus optional order/where
#   figure:   before/after panels, each a list of (SQL returning geom [, cls], css class)
#             Sources are available as tables named after their id, in local metres.
# ---------------------------------------------------------------------------

BG_DISTRICTS = ("SELECT geom FROM districts", "bg")
DISTRICT_LABELS = "districts"

EXAMPLES: dict[str, dict] = {
    "spatial_join": {
        "pipeline": {"base": "places", "steps": [
            {"type": "spatial_join", "source": "districts", "predicate": "intersects",
             "fields": {"district": "district_name"}},
        ]},
        "table": [("name", "name"), ("district", "district")],
        "figure": {
            "before": [("SELECT geom FROM districts", "ref"), ("SELECT geom FROM places", "base")],
            "after": [BG_DISTRICTS,
                      ("SELECT geom, CASE WHEN district IS NULL THEN 'miss' ELSE 'out' END FROM out", None)],
        },
    },
    "spatial_join_overlap": {
        "pipeline": {"base": "parks", "steps": [
            {"type": "spatial_join", "source": "districts",
             "fields": {"district_first": "district_name"}},
            {"type": "spatial_join", "source": "districts", "on_multiple": "largest_overlap",
             "fields": {"district_largest": "district_name"}},
        ]},
        "table": [("name", "name"), ("district_first", "district_first"), ("district_largest", "district_largest")],
        "figure": {
            "before": [("SELECT geom FROM districts", "ref"), ("SELECT geom FROM parks", "base")],
        },
    },
    "spatial_join_all": {
        "pipeline": {"base": "parks", "steps": [
            {"type": "spatial_join", "source": "districts", "match": "all",
             "fields": {"district": "district_name"}},
        ]},
        "table": [("name", "name"), ("district", "district")],
        "order": "name, district",
    },
    "attribute_join": {
        "pipeline": {"base": "places", "steps": [
            {"type": "attribute_join", "source": "categories", "left": "category", "right": "code",
             "fields": {"category_label": "label"}},
        ]},
        "table": [("name", "name"), ("category", "category"), ("category_label", "category_label")],
    },
    "nearest_neighbor": {
        "pipeline": {"base": "places", "steps": [
            {"type": "nearest_neighbor", "source": "stops", "max_distance": 300,
             "distance_field": "stop_distance_m", "fields": {"nearest_stop": "stop_name"}},
        ]},
        "table": [("name", "name"), ("nearest_stop", "nearest_stop"),
                  ("stop_distance_m", "round(stop_distance_m, 1)")],
        "figure": {
            "before": [BG_DISTRICTS, ("SELECT geom FROM stops", "ref"), ("SELECT geom FROM places", "base")],
            "after": [BG_DISTRICTS,
                      ("SELECT ST_MakeLine(o.geom, s.geom) FROM out o JOIN stops s ON s.stop_name = o.nearest_stop", "link"),
                      ("SELECT geom FROM stops", "ref"),
                      ("SELECT geom, CASE WHEN nearest_stop IS NULL THEN 'miss' ELSE 'out' END FROM out", None)],
        },
    },
    "buffer": {
        "pipeline": {"base": "stops", "steps": [{"type": "buffer", "distance": 150}]},
        "figure": {
            "before": [BG_DISTRICTS, ("SELECT geom FROM stops", "base")],
            "after": [BG_DISTRICTS, ("SELECT geom FROM out", "out"), ("SELECT geom FROM stops", "ghostpt")],
        },
    },
    "centroid": {
        "pipeline": {"base": "parks", "steps": [{"type": "centroid"}]},
        "figure": {
            "before": [BG_DISTRICTS, ("SELECT geom FROM parks", "base")],
            "after": [BG_DISTRICTS, ("SELECT geom FROM parks", "ghost"), ("SELECT geom FROM out", "out")],
        },
    },
    "clip": {
        "pipeline": {
            "derived_sources": [{"id": "old_puddleton", "from": "districts", "where": "district_name = 'Old Puddleton'"}],
            "base": "parks",
            "steps": [{"type": "clip", "source": "old_puddleton"}],
        },
        "table": [("name", "name"), ("area_m2", "round(ST_Area(geom), -2)")],
        "figure": {
            "before": [BG_DISTRICTS, ("SELECT geom FROM districts WHERE district_name = 'Old Puddleton'", "ref"),
                       ("SELECT geom FROM parks", "base")],
            "after": [BG_DISTRICTS, ("SELECT geom FROM districts WHERE district_name = 'Old Puddleton'", "refline"),
                      ("SELECT geom FROM parks", "ghost"), ("SELECT geom FROM out", "out")],
        },
    },
    "erase": {
        "pipeline": {"base": "parks", "steps": [{"type": "erase", "source": "river"}]},
        "table": [("name", "name"), ("area_m2", "round(ST_Area(geom), -2)")],
        "figure": {
            "before": [BG_DISTRICTS, ("SELECT geom FROM river", "water"), ("SELECT geom FROM parks", "base")],
            "after": [BG_DISTRICTS, ("SELECT geom FROM river", "waterline"), ("SELECT geom FROM out", "out")],
        },
    },
    "dissolve": {
        "pipeline": {"base": "districts", "steps": [{"type": "dissolve", "by": ["ward"]}]},
        "table": [("ward", "ward"), ("area_ha", "round(ST_Area(geom) / 10000, 1)")],
        "figure": {
            "before": [("SELECT geom FROM districts", "base")],
            "after": [("SELECT geom FROM out", "out")],
            "labels": {"before": "SELECT district_name, ST_Centroid(geom) FROM districts",
                       "after": "SELECT ward, ST_Centroid(geom) FROM out"},
        },
    },
    "intersect_overlay": {
        "pipeline": {"base": "parks", "steps": [
            {"type": "intersect_overlay", "source": "districts", "fields": {"district": "district_name"}},
        ]},
        "table": [("name", "name"), ("district", "district"), ("area_m2", "round(ST_Area(geom), -2)")],
        "order": "name, district",
        "figure": {
            "before": [("SELECT geom FROM districts", "ref"), ("SELECT geom FROM parks", "base")],
            "after": [BG_DISTRICTS, ("SELECT geom FROM out", "out")],
        },
    },
    "filter": {
        "pipeline": {"base": "places", "steps": [
            {"type": "spatial_join", "source": "districts", "fields": {"district": "district_name"}},
            {"type": "filter", "where": "district IS NOT NULL AND bread_crumbs >= 20000"},
        ]},
        "table": [("name", "name"), ("district", "district"), ("bread_crumbs", "bread_crumbs")],
        "figure": {
            "before": [BG_DISTRICTS, ("SELECT geom FROM places", "base")],
            "after": [BG_DISTRICTS, ("SELECT geom FROM places", "ghostpt"), ("SELECT geom FROM out", "out")],
        },
    },
    "merge": {
        "pipeline": {"base": "places", "steps": [{"type": "merge", "source": "new_places"}]},
        "table": [("name", "name"), ("category", "category"), ("opened", "opened"), ("bread_crumbs", "bread_crumbs")],
        "where": "name IN ('Harbour Fish Market', 'Pop-up Duck Gallery', 'Mallard Museum')",
        "figure": {
            "before": [BG_DISTRICTS, ("SELECT geom FROM places", "base"), ("SELECT geom FROM new_places", "ref")],
            "after": [BG_DISTRICTS, ("SELECT geom FROM out", "out")],
        },
    },
    "snapshot": {
        "pipeline": {"base": "places", "steps": [
            {"type": "snapshot", "id": "crumb_hotspots"},
            {"type": "filter", "branch": "crumb_hotspots", "where": "bread_crumbs >= 50000"},
            {"type": "buffer", "branch": "crumb_hotspots", "distance": 250},
            {"type": "spatial_join", "source": "crumb_hotspots", "predicate": "within", "match": "all",
             "fields": {"near_hotspot": "name"}},
            {"type": "filter", "where": "near_hotspot IS NULL OR near_hotspot <> name"},
        ]},
        "table": [("name", "name"), ("near_hotspot", "near_hotspot")],
        "where": "near_hotspot IS NOT NULL",
        "order": "name, near_hotspot",
        "figure": {
            "before": [BG_DISTRICTS, ("SELECT geom FROM places", "base")],
            "after": [BG_DISTRICTS,
                      ("SELECT ST_Buffer(geom, 250) FROM places WHERE bread_crumbs >= 50000", "ref"),
                      ("SELECT DISTINCT ON (name) geom, CASE WHEN near_hotspot IS NULL THEN 'miss' ELSE 'out' END FROM out", None)],
        },
    },
}

# Mapping tutorial: one places pipeline (with a district join so expressions have something to
# combine) and one parks pipeline for area/length.
MAPPING_STEPS = [{"type": "spatial_join", "source": "districts", "fields": {"district": "district_name"}}]

MAPPING_SECTIONS: dict[str, dict] = {
    "map_basic": {
        "mapping": [
            {"to": "name", "from": "name"},
            {"to": "dataset", "const": "Pondsworth open data"},
            {"to": "name_upper", "expr": "upper(name)"},
            {"to": "size", "expr": "CASE\n"
                                   "  WHEN bread_crumbs >= 50000 THEN 'large'\n"
                                   "  WHEN bread_crumbs >= 15000 THEN 'medium'\n"
                                   "  WHEN bread_crumbs IS NULL THEN 'unknown'\n"
                                   "  ELSE 'small'\n"
                                   "END"},
            {"to": "label", "expr": "name || ' (' || coalesce(district, 'outside town') || ')'"},
        ],
        "table": [("name", "name"), ("dataset", "dataset"), ("name_upper", "name_upper"),
                  ("size", "size"), ("label", "label")],
        "limit": 5,
    },
    "map_func_geo": {
        "mapping": [
            {"to": "name", "from": "name"},
            {"to": "longitude", "func": "lon"},
            {"to": "latitude", "func": "lat"},
            {"to": "grid_ref", "func": "mgrs"},
            {"to": "geom_wkb", "func": "wkb"},
        ],
        "table": [("name", "name"), ("longitude", "longitude"), ("latitude", "latitude"),
                  ("grid_ref", "grid_ref"), ("geom_wkb", "left(geom_wkb, 22) || '…'")],
        "limit": 3,
    },
    "map_func_misc": {
        "mapping": [
            {"to": "name", "from": "name"},
            {"to": "id", "func": "uuid"},
            {"to": "loaded_at", "func": "now"},
            {"to": "loaded_on", "func": "today"},
        ],
        "table": [("name", "name"), ("id", "id"), ("loaded_at", "strftime(loaded_at, '%Y-%m-%d %H:%M:%S')"),
                  ("loaded_on", "loaded_on")],
        "limit": 3,
    },
    "map_codelist_rules": {
        "mapping": [
            {"to": "name", "from": "name"},
            {"to": "category_code", "from": "category"},
            {"to": "category_name", "codelist": {
                "source": "category",
                "cases": [
                    {"match": "MUS", "value": "Museum"},
                    {"match": "LIB", "value": "Library"},
                    {"like": "CA%", "value": "Café"},
                    {"regex": "^(SCH|UNI)$", "value": "Education"},
                    {"is_blank": True, "value": "Not categorised"},
                ],
                "default": "Other",
            }},
        ],
        "table": [("name", "name"), ("category_code", "coalesce(category_code, '')"), ("category_name", "category_name")],
    },
    "map_codelist_file": {
        "mapping": [
            {"to": "name", "from": "name"},
            {"to": "category_code", "from": "category"},
            {"to": "category_name", "codelist": {
                "source": "category",
                "file": f"{DATA_REL}/categories.csv",
                "file_match_col": "code",
                "file_value_col": "label",
                "default": "Unknown category",
            }},
        ],
        "table": [("name", "name"), ("category_code", "coalesce(category_code, '')"), ("category_name", "category_name")],
    },
    "map_cast": {
        "mapping": [
            {"to": "name", "from": "name"},
            {"to": "opened_text", "from": "opened"},
            {"to": "opened_year", "from": "opened", "cast": "INTEGER"},
            {"to": "inspected_text", "from": "inspected"},
            {"to": "inspected_date", "from": "inspected", "cast": "DATE"},
        ],
        "table": [("name", "name"), ("opened_text", "opened_text"), ("opened_year", "opened_year"),
                  ("inspected_text", "inspected_text"), ("inspected_date", "inspected_date")],
    },
}

MAPPING_AREA = {
    "map_area": {
        "base": "parks",
        "mapping": [
            {"to": "name", "from": "name"},
            {"to": "area_m2", "func": "area"},
            {"to": "perimeter_m", "func": "length"},
            {"to": "area_ha", "expr": "round(ST_Area(geom) / 10000, 2)"},
        ],
        "table": [("name", "name"), ("area_m2", "round(area_m2, -2)"), ("perimeter_m", "round(perimeter_m)"),
                  ("area_ha", "area_ha")],
    },
}

OUTPUT = "output/pondsworth_tutorial.gpkg"


def _pipeline(name: str, part: dict) -> dict:
    p = {"name": name, "working_crs": UTM, "sources": SOURCES}
    if "derived_sources" in part:
        p["derived_sources"] = part["derived_sources"]
    p["base"] = part["base"]
    p["steps"] = part.get("steps", [])
    p["mapping"] = part.get("mapping", [])
    p["layers"] = [{"layer": name, "crs": UTM}]
    return p


def build_config() -> dict:
    pipelines = [_pipeline(n, ex["pipeline"]) for n, ex in EXAMPLES.items()]
    pipelines += [_pipeline(n, {"base": "places", "steps": MAPPING_STEPS, "mapping": s["mapping"]})
                  for n, s in MAPPING_SECTIONS.items()]
    pipelines += [_pipeline(n, {"base": s["base"], "mapping": s["mapping"]}) for n, s in MAPPING_AREA.items()]
    return {
        "name": "pondsworth_tutorial",
        "description": "Every example from the duck soup tutorials, one pipeline (and layer) each.",
        "output": OUTPUT,
        "overwrite": True,
        "pipelines": pipelines,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data):  # write the shared source list out in full every time
        return True


_PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_./:-]*$")


def _str(d, s):
    # Quote anything that isn't a bare identifier/path, like hand-written pipeline YAML does;
    # multi-line text (a formatted SQL expression) as a literal block.
    if "\n" in s:
        return d.represent_scalar("tag:yaml.org,2002:str", s, style="|")
    return d.represent_scalar("tag:yaml.org,2002:str", s, style=None if _PLAIN.match(s) else '"')


def _dict(d, data):
    # {to: x, from: y} on one line when every value is a scalar and it fits; block otherwise.
    flat = all(not isinstance(v, (dict, list)) for v in data.values())
    flow = flat and len(str(data)) < 90
    return d.represent_mapping("tag:yaml.org,2002:map", data, flow_style=flow)


_Dumper.add_representer(str, _str)
_Dumper.add_representer(dict, _dict)


def _yaml(obj) -> str:
    # Flow style for leaf mappings ({to: x, from: y}) reads like the hand-written docs.
    return yaml.dump(obj, Dumper=_Dumper, sort_keys=False, default_flow_style=False,
                     allow_unicode=True, width=1000)


def _fmt(v) -> str:
    if v is None:
        return "*NULL*"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v)
    return s.replace("|", "\\|") if s else "*''*"


def _table(con, cols, where=None, order=None, limit=None) -> str:
    sql = f"SELECT {', '.join(expr for _, expr in cols)} FROM out"
    if where:
        sql += f" WHERE {where}"
    sql += f" ORDER BY {order or 'rowid'}"
    if limit:
        sql += f" LIMIT {limit}"
    rows = con.execute(sql).fetchall()
    head = "| " + " | ".join(f"`{h}`" for h, _ in cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    body = ["| " + " | ".join(_fmt(v) for v in r) + " |" for r in rows]
    return "\n".join([head, sep, *body]) + "\n"


# Drawing frame (local metres) and panel size (px).
FX0, FY0, FX1, FY1 = -60, -60, 1310, 860
PW = 360
PH = PW * (FY1 - FY0) / (FX1 - FX0)
GAP = 28
TOP = 22


def _xy(x, y, ox):
    return (ox + (x - FX0) / (FX1 - FX0) * PW, TOP + (FY1 - y) / (FY1 - FY0) * PH)


def _svg_geom(con, wkt_geom_sql: str, cls_default: str | None, ox: float) -> list[str]:
    con.execute(f"CREATE OR REPLACE TEMP VIEW _layer AS {wkt_geom_sql}")
    names = [r[0] for r in con.execute("DESCRIBE _layer").fetchall()]
    cls_col = f', "{names[1]}"' if len(names) > 1 else ""
    out = []
    for gj, *rest in con.execute(f'SELECT ST_AsGeoJSON("{names[0]}"){cls_col} FROM _layer').fetchall():
        if gj is None:
            continue
        cls = rest[0] if rest and rest[0] else cls_default
        out.extend(_svg_from_geojson(json.loads(gj), cls, ox))
    return out


def _svg_from_geojson(gj, cls, ox) -> list[str]:
    t = gj["type"]
    if t == "Point":
        x, y = _xy(*gj["coordinates"], ox)
        r = 3.2 if cls in ("ghostpt",) else 4.2
        return [f'<circle class="{cls}" cx="{x:.1f}" cy="{y:.1f}" r="{r}"/>']
    if t in ("LineString",):
        pts = " ".join(f"{a:.1f},{b:.1f}" for a, b in (_xy(*c, ox) for c in gj["coordinates"]))
        return [f'<polyline class="{cls}" points="{pts}"/>']
    if t == "Polygon":
        d = ""
        for ring in gj["coordinates"]:
            pts = [_xy(*c, ox) for c in ring]
            d += "M" + "L".join(f"{a:.1f},{b:.1f}" for a, b in pts) + "Z"
        return [f'<path class="{cls}" d="{d}"/>']
    if t.startswith("Multi") or t == "GeometryCollection":
        parts = gj.get("geometries") or [
            {"type": t[5:], "coordinates": c} for c in gj["coordinates"]
        ]
        return [s for p in parts for s in _svg_from_geojson(p, cls, ox)]
    return []


def _svg_labels(con, sql, ox) -> list[str]:
    out = []
    con.execute(f"CREATE OR REPLACE TEMP VIEW _labels AS {sql}")
    text_col, geom_col = [r[0] for r in con.execute("DESCRIBE _labels").fetchall()][:2]
    for text, gj in con.execute(f'SELECT "{text_col}", ST_AsGeoJSON("{geom_col}") FROM _labels').fetchall():
        x, y = json.loads(gj)["coordinates"]
        px, py = _xy(x, y, ox)
        out.append(f'<text class="lbl" x="{px:.1f}" y="{py:.1f}" text-anchor="middle">{text}</text>')
    return out


def _figure(con, fig: dict, title: str) -> str:
    panels = [("Before", fig["before"])]
    if "after" in fig:
        panels.append(("After", fig["after"]))
    width = PW * len(panels) + GAP * (len(panels) - 1)
    height = TOP + PH + 4
    parts = [f'<svg class="ds-diagram" viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="{title}">']
    for i, (label, layers) in enumerate(panels):
        ox = i * (PW + GAP)
        clip_id = f"ds-{title}-{i}"  # unique per page: several diagrams share one document
        parts.append(f'<text class="hd" x="{ox + 2}" y="14">{label}</text>')
        parts.append(f'<clipPath id="{clip_id}"><rect x="{ox}" y="{TOP}" width="{PW}" height="{PH:.1f}" rx="6"/></clipPath>')
        parts.append(f'<rect class="frame" x="{ox}" y="{TOP}" width="{PW}" height="{PH:.1f}" rx="6"/>')
        parts.append(f'<g clip-path="url(#{clip_id})">')
        for sql, cls in layers:
            parts.extend(_svg_geom(con, sql, cls, ox))
        lbl = fig.get("labels", {}).get(label.lower())
        if lbl:
            parts.extend(_svg_labels(con, lbl, ox))
        parts.append("</g>")
    parts.append("</svg>")
    # One line, so Markdown treats it as a single raw HTML block.
    return "".join(parts) + "\n"


def _load_sources(con) -> None:
    for s in SOURCES:
        if s["format"] != "geojson":
            continue
        con.execute(
            f"CREATE OR REPLACE TABLE {s['id']} AS SELECT * EXCLUDE (geom), "
            f"ST_Translate(ST_Transform(geom, 'EPSG:4326', '{UTM}', always_xy := true), {-X0}, {-Y0}) AS geom "
            f"FROM ST_Read('{(ROOT / s['uri']).as_posix()}')"
        )


def _load_out(con, gpkg: Path, layer: str) -> None:
    con.execute(
        f"CREATE OR REPLACE TABLE out AS SELECT * EXCLUDE (geom), ST_Translate(geom, {-X0}, {-Y0}) AS geom "
        f"FROM ST_Read('{gpkg.as_posix()}', layer='{layer}')"
    )


def main() -> None:
    write_data()
    cfg_dict = build_config()
    (DATA / "pondsworth.yaml").write_text(
        "# Generated by scripts/build_tutorial.py — every example from the tutorials.\n"
        "# Run from the repo root: duck-soup run data/tutorial/pondsworth.yaml\n" + _yaml(cfg_dict),
        encoding="utf-8",
    )
    cfg = load_config_dict(cfg_dict)

    GEN.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        gpkg = Path(tmp) / "tutorial.gpkg"
        cfg = cfg.model_copy(update={"output": str(gpkg)})
        run_config(cfg, log=lambda m: None)

        con = _con()
        _load_sources(con)

        for name, ex in EXAMPLES.items():
            (GEN / f"{name}.yaml").write_text(_yaml(ex["pipeline"]), encoding="utf-8")
            _load_out(con, gpkg, name)
            if "table" in ex:
                (GEN / f"{name}.md").write_text(
                    _table(con, ex["table"], ex.get("where"), ex.get("order")), encoding="utf-8")
            if "figure" in ex:
                (GEN / f"{name}.svg").write_text(_figure(con, ex["figure"], name), encoding="utf-8")
            print("example", name)

        for name, sec in {**MAPPING_SECTIONS, **MAPPING_AREA}.items():
            (GEN / f"{name}.yaml").write_text(_yaml({"mapping": sec["mapping"]}), encoding="utf-8")
            _load_out(con, gpkg, name)
            (GEN / f"{name}.md").write_text(
                _table(con, sec["table"], limit=sec.get("limit")), encoding="utf-8")
            print("mapping", name)

        # Overview map of the sample data for the tutorial landing page.
        overview = {
            "before": [("SELECT geom FROM districts", "ref"), ("SELECT geom FROM river", "water"),
                       ("SELECT geom FROM parks", "park"), ("SELECT geom FROM stops", "stop"),
                       ("SELECT geom FROM places", "base"), ("SELECT geom FROM new_places", "ghostpt")],
            "labels": {"before": "SELECT district_name, ST_Translate(ST_Centroid(geom), 0, "
                                 "CASE WHEN ward = 'Upstream' THEN 150 ELSE -165 END) FROM districts"},
        }
        (GEN / "overview.svg").write_text(
            _figure(con, overview, "overview").replace(">Before<", "><"), encoding="utf-8")
        con.close()


if __name__ == "__main__":
    main()
