# Mapping & codelists

`mapping` is an ordered list of output columns. Each item has a `to` (the output column name)
and **exactly one** value source.

!!! tip "See them in action"
    [Mapping by example](../tutorials/mapping.md) walks through every value source, `func` and
    `cast` with real output from a sample dataset.

```yaml
mapping:
  - {to: name,        from: road_name}
  - {to: dataset,     const: "OpenStreetMap"}
  - {to: label,       expr: "road_name || ' (' || city || ')'"}
  - {to: id,          func: uuid}
  - {to: lanes,       from: lane_count, cast: INTEGER}
  - to: road_class
    codelist:
      source: class_code
      cases:
        - {match: "M", value: "Motorway"}
        - {match: "A", value: "Primary road"}
      default: "Other"
```

An empty `mapping: []` writes every upstream column unchanged. The output geometry is always
written as `geom`, and `geom` can't be used as a `to`.

## Value sources

| Key | Meaning |
|---|---|
| `from` | Copy a column from the row (base columns plus fields pulled by steps). |
| `const` | A literal: string, number or boolean. |
| `expr` | Any DuckDB SQL expression over the row's columns. Multi-line expressions are fine (use a YAML `|` block). |
| `func` | A built-in, see below. |
| `codelist` | Translate a column's value, see [Codelists](#codelists). |

### `cast`

Any item can add `cast: <SQL type>` (`INTEGER`, `DOUBLE`, `VARCHAR`, `BOOLEAN`, `DATE`,
`TIMESTAMP`, …). It's applied as `TRY_CAST`, so a value that can't be converted becomes NULL
instead of failing the run.

### `func`

| `func` | Output |
|---|---|
| `uuid` | A random UUIDv4 string per row. |
| `now` | Current timestamp. |
| `today` | Current date. |
| `lon` | Longitude (EPSG:4326) of the feature's centroid, 7 decimals. |
| `lat` | Latitude (EPSG:4326) of the feature's centroid, 7 decimals. |
| `mgrs` | MGRS grid reference of the centroid. |
| `wkb` | The geometry in EPSG:4326 as hex WKB. |
| `area` | `ST_Area` in working-CRS units (m² in a metric CRS). |
| `length` | `ST_Length` in working-CRS units: perimeter for polygons. |

Centroids are computed in the working CRS and then reprojected, so `lon`/`lat` stay accurate
for long lines and large polygons. `area` and `length` require a projected working CRS. All
geometry functions require a spatial base source.

## Codelists

A codelist translates one column's values into another set of values: the YAML equivalent of
a lookup table or a `CASE WHEN`.

### Rules

```yaml
- to: species
  codelist:
    source: raw_species        # the column being translated
    case_insensitive: true     # default true
    cases:
      - {match: "mall",     value: "Mallard"}    # exact match
      - {like:  "%gadwall%", value: "Gadwall"}   # SQL LIKE pattern
      - {regex: "^teal",    value: "Teal"}       # regular expression
      - {is_blank: true,    value: "Unknown"}    # NULL or ''
    default: "Other"           # when no rule matches; omit for NULL
```

- Rules are evaluated **top to bottom, first match wins**.
- Each case has exactly one of `match`, `like`, `regex` or `is_blank: true`.
- `is_blank` lets an empty value get its own result, in order with the other rules. Without
  it, an empty value falls through to `default` like any other unmatched value.

### File lookup

For big code tables, point at a CSV instead of listing rules:

```yaml
- to: species_name
  codelist:
    source: species_code
    file: codelists/species.csv
    file_match_col: code          # key column in the CSV
    file_value_col: common_name   # value column in the CSV
    default: "Unidentified"
```

A codelist has either `cases` or `file`, not both.

!!! tip "Codelist or attribute join?"
    A codelist produces **one** translated column. If you need several columns from the same
    lookup table, add an [`attribute_join`](steps.md#attribute_join) step instead and map its
    fields. It's a real join and more efficient for that case.

## Per-layer mapping

A layer can override the pipeline mapping with its own:

```yaml
layers:
  - layer: full
    crs: EPSG:25833
  - layer: slim
    crs: EPSG:4326
    mapping:
      - {to: name, from: road_name}
      - {to: id,   func: uuid}
```

This is YAML-only for now: the editor doesn't show per-layer mappings.
