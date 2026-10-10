# Mapping by example

The **mapping** decides exactly which columns a layer gets, what they're called, and what goes
in them. Each row of the mapping creates one output column from one *value source*:

| Value source | Fills the column with | Example |
|---|---|---|
| [`from`](#from-copy-a-column) | a column that's already there | `{to: name, from: name}` |
| [`const`](#const-a-fixed-value) | the same value on every row | `{to: dataset, const: "Pondsworth open data"}` |
| [`expr`](#expr-a-sql-expression) | a SQL expression you write | `{to: name_upper, expr: "upper(name)"}` |
| [`func`](#func-built-in-values) | a built-in calculation | `{to: id, func: uuid}` |
| [`codelist`](#codelist-translate-codes) | a translation of a column's values | see below |

Any of them can add [`cast`](#cast-set-the-column-type) to convert the result to a specific
type.

The examples run on the [Pondsworth places](index.md), after a spatial join that adds each
place's `district`. They're the `map_*` pipelines in `pipelines/pondsworth.yaml` (from [`duck-soup tutorial`](index.md#get-the-data)). In the
editor, these are the **value source** and **cast** columns of the
[mapping grid](../editor/mapping.md).

!!! note "No mapping at all"
    A layer with an empty mapping gets every column as-is. Add a mapping as soon as you want to
    choose, rename, reorder or compute columns. Once there is a mapping, only the columns in it
    are written. The geometry is always written, as `geom`, so don't map it yourself.

## `from`: copy a column

```yaml
- {to: name, from: name}
- {to: place_name, from: name}     # the same column under a new name
```

`from` copies a column that exists at the end of the steps: one from the base source, or one a
join step pulled in through its `fields`. `to` is the name it gets in the output. Mapping
rows are written in order, so the mapping also sets the column order.

## `const`: a fixed value

```yaml
- {to: dataset, const: "Pondsworth open data"}
- {to: version, const: 2}
```

Every row gets the same value. Handy for provenance columns ("where did this come from?")
or for a column a target schema requires but your data doesn't have.

## `expr`: a SQL expression

`expr` takes any [DuckDB SQL expression](https://duckdb.org/docs/sql/expressions/overview)
that can be computed from a single row's columns.

```yaml
--8<-- "docs/tutorials/generated/map_basic.yaml"
```

--8<-- "docs/tutorials/generated/map_basic.md"

How these work:

- **`upper(name)`** calls a function on a column. DuckDB has hundreds: text (`lower`, `trim`,
  `replace`, `regexp_replace`, `split_part`), numbers (`round`, `abs`, `greatest`), dates
  (`year`, `strftime`, `date_diff`) and more.
- **`CASE WHEN … THEN … ELSE … END`** picks a value by testing conditions top to bottom; the
  first true one wins. Nobody has counted the crumbs at the Duckling Swim School, so it falls
  into the `IS NULL` branch. A long expression can span several lines, as here (YAML's `|`
  keeps the line breaks).
- **`||`** joins text. If any part is NULL, the whole result is NULL. That's why the label uses
  `coalesce(district, 'outside town')`: the Old Lighthouse has no district, and without
  `coalesce` its label would be empty instead of "Old Lighthouse (outside town)".

The rules of SQL apply. Text values go in **single quotes** (`'large'`). Column names can be
written bare, or in **double quotes** if they have spaces or capitals (`"Opened Year"`). In the
YAML, wrap the whole expression in double quotes, as above. If the expression itself contains
double quotes, write it as a `|` block instead, which needs no quoting.

The editor's [expression builder](../editor/mapping.md#expression-builder) lists the
available columns and common functions, and checks your expression against real rows as you
type.

!!! tip "Spatial functions work too"
    The row's geometry is available as `geom`, in the working CRS, so expressions like
    `round(ST_Area(geom) / 10000, 2)` (hectares) or `ST_GeometryType(geom)` work. See the
    [area example](#area-and-length) below.

## `func`: built-in values

`func` covers values that are awkward or impossible to write as an expression.

### Coordinates and geometry

```yaml
--8<-- "docs/tutorials/generated/map_func_geo.yaml"
```

--8<-- "docs/tutorials/generated/map_func_geo.md"

| `func` | Gives | How it's calculated |
|---|---|---|
| `lon`, `lat` | longitude / latitude in WGS84 (EPSG:4326), 7 decimals | The centroid of the geometry, taken in the working CRS and then converted to lon/lat. For lines and polygons you get the middle, not the first vertex. |
| `mgrs` | an [MGRS](https://en.wikipedia.org/wiki/Military_Grid_Reference_System) grid reference to 1 m | From the same centroid. `31UET0019900649` is zone `31U`, square `ET`, then a 5-digit easting and northing. |
| `wkb` | the geometry as hex-encoded WKB, in EPSG:4326 | The full geometry (not the centroid), for systems that expect geometry in a text column. |

```yaml
--8<-- "docs/tutorials/generated/map_func_xy.yaml"
```

--8<-- "docs/tutorials/generated/map_func_xy.md"

| `func` | Gives | How it's calculated |
|---|---|---|
| `x`, `y` | easting / northing in the working CRS, 3 decimals | The same centroid as `lon`/`lat`, before it's converted. Use these when the target system wants projected coordinates (UTM, say) rather than lon/lat. |
| `geohash` | a [geohash](https://en.wikipedia.org/wiki/Geohash) of the centroid | Nearby features share a prefix, so it works as a sortable location code or for grouping by area. |
| `geom_type` | the geometry type: `POINT`, `LINESTRING`, `POLYGON`, `MULTIPOLYGON`, … | Handy when a source mixes geometry types and the target needs to tell them apart. |
| `wkt` | the geometry as WKT text, in EPSG:4326 | Like `wkb`, but readable. |

These work whatever the working CRS is, and need a base source with geometry.

### IDs and timestamps

```yaml
--8<-- "docs/tutorials/generated/map_func_misc.yaml"
```

--8<-- "docs/tutorials/generated/map_func_misc.md"

| `func` | Gives |
|---|---|
| `seq` | a row number, 1, 2, 3, … in the order rows are written, counted separately for each output layer. Like `uuid`, it follows the data, not the feature: if the source order changes, so do the numbers. |
| `uuid` | a random [UUID](https://en.wikipedia.org/wiki/Universally_unique_identifier) (version 4) per row, new on every run. Use it when a target system needs unique IDs, but note that it won't stay the same for a feature between runs. |
| `now` | the date and time the run started, the same on every row |
| `today` | the date the run started |

### Area and length

```yaml
--8<-- "docs/tutorials/generated/map_area.yaml"
```

--8<-- "docs/tutorials/generated/map_area.md"

| `func` | Gives |
|---|---|
| `area` | the area of polygons, in working-CRS units (m² with a metric CRS); 0 for points and lines |
| `length` | the length of lines, or the perimeter of polygons, in working-CRS units |

Both are measured in the working CRS, so they need a projected one: duck soup refuses to
compute them in degrees. For other units, use an `expr` like `area_ha` above.

## `codelist`: translate codes

A codelist turns one column's values into other values, such as category codes into readable
names. There are two kinds: rules you write in the YAML, or a lookup in a CSV file.

### Codelist rules

```yaml
--8<-- "docs/tutorials/generated/map_codelist_rules.yaml"
```

--8<-- "docs/tutorials/generated/map_codelist_rules.md"

- `source` is the column being translated.
- Each case has one way of matching and a `value` to output:

    | Match type | Matches when the source value… | Here |
    |---|---|---|
    | `match` | equals the text exactly | `MUS` → Museum |
    | `like` | fits a SQL LIKE pattern: `%` is any text, `_` one character | `CA%` → Café |
    | `regex` | matches a regular expression | `^(SCH\|UNI)$` → Education |
    | `is_blank: true` | is NULL or empty text | Old Duck House → Not categorised |

- **Cases are checked top to bottom, and the first match wins.** Put specific rules before
  general ones.
- `default` is used when nothing matches (Breadcrumb Hill's `PRK`, the Old Lighthouse's `LND`).
  Without a `default`, unmatched values become NULL.
- `case_insensitive` is on unless you set it to `false`, so `mus` would match `MUS` too.

### Codelist from a CSV file

For long code tables, keep the codes in a CSV file instead of the YAML:

```yaml
--8<-- "docs/tutorials/generated/map_codelist_file.yaml"
```

--8<-- "docs/tutorials/generated/map_codelist_file.md"

- `file` is a CSV with a header row; `file_match_col` is the column compared with `source`,
  and `file_value_col` is the column returned.
- Matching ignores case unless `case_insensitive: false`. Values are compared as text. If a key
  appears more than once in the file, only one of its rows is used, so keep keys unique.
- `default` works as with rules. Here both the blank category and `LND` get "Unknown
  category".

!!! tip "Codelist or attribute join?"
    A codelist gives you **one** translated column. When you need several columns from the same
    table (a label *and* a description *and* an owner), use an
    [`attribute_join`](steps.md#attribute_join) step and map its fields instead.

## `cast`: set the column type

Every value has a type: text, integer, decimal number, date, and so on. `cast` converts a
mapping's result to the type you name, and the output layer gets a column of that type.

```yaml
--8<-- "docs/tutorials/generated/map_cast.yaml"
```

--8<-- "docs/tutorials/generated/map_cast.md"

Why it matters: Pondsworth's `opened` years are stored as **text**. Written as-is, the output
column is text too: you can't filter it with `opened > 1950` or style it on a graduated scale
in QGIS, and text sorts character by character, so `"950"` would come after `"1887"`. Cast to
`INTEGER`, it becomes a real number column.

How it works:

- duck soup converts with DuckDB's `TRY_CAST`: **a value that can't be converted becomes NULL**
  instead of stopping the run. Breadcrumb Hill's `unknown` and the Duckling Swim School's
  `March 2024` show this. Check for unexpected NULLs after adding a cast.
- `DATE` and `TIMESTAMP` accept ISO dates (`2024-03-14`) and also day.month.year
  (`14.03.2024`), which is common in European data.
- `cast` works with every value source. It's applied last, to the finished value of a `from`,
  `const`, `expr`, `func` or `codelist`.

Common types:

| `cast` | For |
|---|---|
| `INTEGER` / `BIGINT` | whole numbers (`BIGINT` for values over about 2.1 billion, or a very generous duck) |
| `DOUBLE` | decimal numbers |
| `VARCHAR` | text, such as codes with leading zeros (`0042`) that must stay text |
| `BOOLEAN` | true/false (accepts `true`/`false`, `t`/`f`, `yes`/`no`, `1`/`0`) |
| `DATE` | dates |
| `TIMESTAMP` | date and time |

Any [DuckDB type](https://duckdb.org/docs/sql/data_types/overview) name works, such as
`DECIMAL(10,2)`.

!!! warning "Dates before 1970 in GeoPackage"
    GeoPackage output can't store `DATE` values before 1970 through duck soup's writer. For
    older dates, cast to `TIMESTAMP` instead.
