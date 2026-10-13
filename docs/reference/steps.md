# Step types

Steps run in order, each one transforming the rows produced by the step before it (or by the
base source, for the first step). Every step type accepts an optional [`branch`](#branch) key,
and `spatial_join`, `attribute_join`, `nearest_neighbor`, `http_lookup`, `make_points`, `clip`, `filter` and `remove_duplicates` an optional
[`rejects`](#rejects) key that sends the rows they'd otherwise drop, or leave unmatched, to a
layer of their own.

!!! tip "See them in action"
    [Steps by example](../tutorials/steps.md) runs every step type on a small sample town, with
    before/after diagrams and the actual output.

| Group | `type` | Row count | Changes geometry | Adds columns |
|---|---|---|---|---|
| Joins | [`spatial_join`](#spatial_join) | same (`match: all`: may grow) | – | ✓ |
| | [`attribute_join`](#attribute_join) | same | – | ✓ |
| | [`nearest_neighbor`](#nearest_neighbor) | same | – | ✓ |
| | [`intersect_overlay`](#intersect_overlay) | may grow or shrink | ✓ | ✓ |
| | [`line_overlay`](#line_overlay) | grows | ✓ | ✓ |
| | [`http_lookup`](#http_lookup) | same | – | ✓ |
| Geometry | [`make_points`](#make_points) | same | ✓ (points) | – |
| | [`buffer`](#buffer) | same | ✓ | – |
| | [`centroid`](#centroid) | same | ✓ | – |
| | [`clip`](#clip) | may shrink | ✓ | – |
| | [`erase`](#erase) | may shrink | ✓ | – |
| | [`dissolve`](#dissolve) | shrinks | ✓ | keeps only `by` and `aggregates` |
| | [`combine_lines`](#combine_lines) | shrinks | ✓ | keeps only `by` and `aggregates` |
| | [`split_lines`](#split_lines) | grows | ✓ | – |
| | [`simplify`](#simplify) | same | ✓ | – |
| | [`snap_to_grid`](#snap_to_grid) | same | ✓ | – |
| | [`clean_coverage`](#clean_coverage) | same | ✓ | – |
| | [`fill_holes`](#fill_holes) | same | ✓ | – |
| | [`eliminate_slivers`](#eliminate_slivers) | shrinks | ✓ | – |
| | [`explode`](#explode) | grows | ✓ | optional part number |
| Rows | [`filter`](#filter) | may shrink | – | – |
| | [`remove_duplicates`](#remove_duplicates) | may shrink | – | – |
| | [`merge`](#merge) | grows | – | ✓ (union of columns) |
| | [`snapshot`](#snapshot) | – | – | – |
| Attributes | [`attributes`](#attributes) | same | – | ✓ (set, overwrite, drop) |

`fields` on join steps is always a map of **`output_name: source_column`**.

---

## `spatial_join`

Copies attributes from features in another source that spatially match each base feature.

```yaml
- type: spatial_join
  source: ponds
  predicate: intersects        # intersects | contains | within
  match: first                 # first | all | summarize
  on_multiple: largest_overlap # first | largest_overlap (only with match: first)
  fields: {pond_name: name, pond_vibe: vibe}
```

| Key | Default | Meaning |
|---|---|---|
| `source` | **required** | Source, derived source or snapshot to join against. |
| `predicate` | `intersects` | Relationship from the base feature's point of view: base *intersects* / *contains* / *is within* the source feature. |
| `match` | `first` | `first`: one row per base feature (NULL fields when nothing matches). `all`: one row per matching pair; base features with no match are kept once, with NULL fields. `summarize`: one row per base feature, each field combining the values of all its matches (see [Summarizing matches](#summarizing-matches)). |
| `on_multiple` | `first` | With `match: first`, which match wins when several do. `first` = the lowest original row order in the source (deterministic). `largest_overlap` = the biggest intersection area (ties fall back to row order). |
| `fields` | `{}` | Columns to copy. |
| `rejects` | – | Layer for the base features with no match, which then leave the chain. See [`rejects`](#rejects). |

!!! warning "`match: first` hides multiple matches"
    A feeding zone that reaches into two ponds silently gets only one of them. Use
    `on_multiple: largest_overlap` for "which one is it mostly in", `match: summarize` to
    count or list them, or `match: all` to see every match. The editor warns when features in
    the sample data match several.

### Summarizing matches

With `match: summarize`, each feature keeps one row, and each field says how to combine the
values of all its matches, the same way as [Keeping other columns](#keeping-other-columns):

```yaml
- type: spatial_join
  source: trees
  match: summarize
  fields:
    tree_count: {how: count}
    species:    {from: species, how: all}
    tallest:    {from: height, how: max}
    crowns_m2:  {expr: "sum(ST_Area(geom))"}
```

A feature with no matches gets a count of 0 and NULL for the rest. `from_largest` takes the
value of the largest matching feature, and an `expr` sees the join source's columns.

## `attribute_join`

A left join on a column value, like a VLOOKUP.

```yaml
- type: attribute_join
  source: species_codes
  left: species_code     # SQL expression on the running row
  right: code            # column on the join source
  fields: {species_name: common_name}
```

| Key | Meaning |
|---|---|
| `source` | The source to look values up in (spatial or tabular). |
| `left` | A SQL expression evaluated on the current row: usually just a column name, but it can be `upper(code)` or a quoted literal like `'Mallard'`. |
| `right` | Column on the join source that must equal `left`. |
| `match` | `first` (default): the first matching row, in the source's row order. `summarize`: each field combines the values of all the matching rows. |
| `fields` | Columns to copy. With `match: summarize`, how to combine each one, as in [Summarizing matches](#summarizing-matches). |
| `rejects` | Layer for the rows with no match, which then leave the chain. See [`rejects`](#rejects). |

With `match: first`, each row picks up at most **one** match: if `right` has duplicate
values, the first of them in the source. The editor warns when rows in the sample data
match several. To use all of them, summarize:

```yaml
- type: attribute_join
  source: inspections
  left: pond_id
  right: pond_id
  match: summarize
  fields:
    inspections: {how: count}
    last_inspected: {from: date, how: max}
```

## `nearest_neighbor`

Copies attributes from the closest feature in another source, whether or not they touch.

```yaml
- type: nearest_neighbor
  source: bread_stalls
  max_distance: 500         # optional search radius (working-CRS units)
  distance_field: waddle_m  # optional output column with the distance
  fields: {nearest_bread: name}
```

Without `max_distance`, every base feature is compared with every source feature, which is
slow on big inputs. Set a sensible radius when you can: it lets DuckDB use its spatial index.
Features with nothing in range get NULL fields, or go to a [`rejects`](#rejects) layer when
one is set. Needs a projected working CRS when `max_distance` or `distance_field` is set.

## `intersect_overlay`

One output row per overlapping (base × source) pair, with the intersection as the geometry.

```yaml
- type: intersect_overlay
  source: feeding_zones
  fields: {zone: name}
```

Base features that overlap nothing are dropped, and pairs that only touch are ignored. Row
count can grow a lot when there are many overlaps.

## `line_overlay`

Line-on-line overlay. The running lines are cut where they lie
on top of the `source` lines: the shared stretches get `fields` from the source, and every
other piece is kept with those fields NULL. The output is still the complete network, so one
`line_overlay` per dataset can be chained to collect attributes from several line layers.

```yaml
- type: line_overlay
  source: speed_limits
  fields: {speed: limit_kmh}
- type: line_overlay
  source: surfaces
  fields: {surface: surface_type}
  tolerance: 0.05    # optional, working-CRS units
```

Each piece becomes its own feature: a line covered in the middle comes out as three lines,
not one multi-line. Neighbouring pieces share their end points exactly, and the geometry stays
the running line's own; the source lines only decide where the cuts go and which attributes a
piece gets. 3D lines keep their heights: positions along the line are measured in 2D, and
each piece carries the Z values of the original line. Lines that only cross a source line are not cut there. Where several source lines
lie on the same stretch, the first one (in source order) wins. Source lines with no running
line under them are not added.

`tolerance` is how far apart two lines may be and still count as lying on each other. Lines
that look identical rarely are: one dataset was digitised separately, has vertices the other
lacks, or went through a reprojection, and they differ by millimetres. Without a `tolerance`
they must match to within 0.000001 units, so those lines get no source fields, except for a
few centimetres where they cross each other. Set it just above the expected difference, e.g.
`0.05` for data in metres. A cut can land up to the tolerance away from where the source line
ends.

## `http_lookup`

Sends an HTTP GET request per row and adds values from the JSON response as columns:
geocoding addresses with an address-search API, say.

```yaml
- type: http_lookup
  url: "https://api.example.com/geocode?q={address}&limit=1"
  records: results            # dot path to the list of hits; the first hit is used
  fields:                     # output_name: dot path inside the hit
    found_address: formatted_address
    lat: location.lat
    lon: location.lon
  status_field: http_status   # optional: the HTTP status code (0: no response)
  rejects: not_found          # optional: rows with no hit
```

- **The URL.** `{column}` in `url` is replaced by that column's value, URL-encoded (`{{` and
  `}}` for literal braces). A row with a NULL there makes no request. To build the URL with
  any SQL instead, make it in an [`attributes`](#attributes) step and give
  `url_column: <column>`: that column is sent as it is. `params` adds query parameters as
  `{name: SQL expression}`, URL-encoded, which reads better than a long template. For a
  reverse geocode, add `lat` and `lon` columns in an [`attributes`](#attributes) step first
  (`func: lat` / `func: lon`) and use `{lat}` and `{lon}` in the URL.
- **The response.** Without `records`, the whole response is the hit. Paths take numbers
  for list items (`categories.0`). Values come out as text: cast them in a later
  [`attributes`](#attributes) step or the mapping (`CAST(lat AS DOUBLE)`). A row whose
  request failed, or whose response has no hit, gets NULL fields, or goes to the `rejects`
  layer when one is set.
- **One request per distinct URL**, and responses are cached on disk, so a URL is not
  requested again while its cached response is younger than `max_age_hours` (default 24;
  set it to null to never ask again). Failed requests (no response, 429, 5xx) are tried 3
  times and asked again by the next run.
- **Be polite to the API.** `max_rate` (requests per second, default 10), `concurrency`
  (requests at once, default 4) and `timeout` (seconds, default 30). `headers` sets request
  headers; `${ENV_VAR}` in a value is read from the environment when the request is sent, so
  an API key stays out of the YAML (and out of the cache).

In the editor, a preview only looks up the first `preview_limit` rows reaching the step
(default 100); the rest continue without a response. It never waits on the network while
computing: URLs it hasn't seen yet are fetched in the background while the status shows
"looking up… 12 / 100 requests", and the preview shows when they're in. After that, editing
anything (the fields, later steps, the mapping) previews as fast as without the step. The
editor's live checks never send requests. A run looks up every row.

## `make_points`

Makes a point from two coordinate columns, so the rows have geometry from this step on, even
when the base source has none: a table of addresses geocoded with an
[`http_lookup`](#http_lookup), say.

```yaml
- type: make_points
  x: lon                  # column or SQL expression; text is cast to a number
  y: lat
  crs: EPSG:4326          # the coordinates' CRS (default EPSG:4326); reprojected to the working CRS
  rejects: no_coordinates # optional: rows without both coordinates
```

A row where either value is missing or not a number gets no geometry, or goes to the
`rejects` layer when one is set. On rows that already have geometry, it's replaced. Geometry
steps and mapping funcs (`buffer`, a spatial join, `lon`/`lat`, `area`, …) work from this
step on; before it, a step that needs geometry stops with an error saying to add one. A
[snapshot](#snapshot) taken before it keeps the rows without geometry.

A CSV or Excel file that already has coordinate columns doesn't need this step: set
`x_field`/`y_field` on the [source](sources.md) instead.

## `buffer`

```yaml
- type: buffer
  distance: 50      # working-CRS units; negative shrinks polygons
```

Requires a projected working CRS.

## `centroid`

```yaml
- type: centroid
```

Replaces each geometry with its centroid point.

## `clip`

Keeps only the part of each feature that overlaps a mask feature.

```yaml
- type: clip
  source: nature_reserve
  predicate: intersects
```

Each feature is clipped against the first mask feature it matches. Features that don't
overlap any mask (or only touch it) are dropped, or kept unclipped in a
[`rejects`](#rejects) layer when one is set.

## `erase`

Removes the part of each feature that overlaps the mask source.

```yaml
- type: erase
  source: reed_beds
  predicate: intersects
```

Each feature has the union of all matching mask features subtracted. Features with no overlap
pass through unchanged, and features erased completely are dropped.

## `dissolve`

Merges geometries that share the same group-by values.

```yaml
- type: dissolve
  by: [pond, species]   # omit or [] to dissolve everything into one feature
```

A `by` entry is a column name, or a SQL expression with a name (`{to, expr}`) to group by a
computed value. `aggregates` keeps other columns, with one value per group:

```yaml
- type: dissolve
  by:
    - pond
    - {to: decade, expr: "floor(year(spotted) / 10) * 10"}
  aggregates:
    - {to: ducks, how: count}
    - {to: crumbs, from: bread_crumbs, how: sum}
    - {to: names, from: name, how: all}
    - {from: species, how: most_common}
```

**Only the `by` keys and the `aggregates` are kept.** Every other column is dropped. See
[Keeping other columns](#keeping-other-columns) for how to choose. Without `by`, the
aggregates are computed over everything, in the one feature that's left.

## `combine_lines`

Joins lines that meet end to end into longer lines. Lines are only joined to lines with the
same `by` values, and each connected chain becomes one feature.

```yaml
- type: combine_lines
  by: [road_name, speed_limit]   # omit or [] to combine all lines
  aggregates:
    - {to: segments, how: count}
    - {from: surface, how: from_largest}
    - {to: length_m, expr: "sum(ST_Length(geom))"}
  preserve_direction: true        # default
```

`by` and `aggregates` work as in [`dissolve`](#dissolve), and **only they are kept**. The
difference is what makes up a feature:

- **One feature per connected chain, not per group.** Two stretches of the same road that
  don't touch stay two features, each a single line.
- **Aggregates are computed per chain**, over the features that make it up: a `count` is
  the number of segments in that chain, not in the whole group.
- **A chain stops where three or more lines meet**, so a street with side roads joining it
  comes out as one line between each pair of junctions.
- **Lines are joined only at their end points.** Lines that cross without sharing a vertex
  (a bridge over a road) are not cut or joined there.
- With **`preserve_direction: true`** (the default), lines are joined only where one ends
  and the next begins, so every chain runs the way its segments did. Leave it on when
  direction matters (one-way streets, measures along the line). Set it to `false` to let a
  line be reversed so it joins one running the other way.

Features that aren't lines (points, polygons, no geometry) are kept as they are, one feature
each, with their aggregates computed over that one feature.

Use `dissolve` instead when you want one feature per group whatever touches what, or for
polygons.

## `split_lines`

Cuts lines where they cross or touch each other, so every crossing becomes a shared end
point: the noding a road or pipe network needs before it's analysed. FME's Intersector,
QGIS's Split lines at intersections and Split with lines.

```yaml
- type: split_lines
  source: municipalities   # optional: cut at this source's lines or polygon edges instead
  tolerance: 0.05          # optional, working-CRS units
```

- **Without `source`**, the lines cut each other: a line is cut wherever another line crosses
  it, ends on it, or starts or stops sharing a stretch with it. A line ending on another
  cuts that one but isn't cut itself. A line isn't cut where it crosses itself.
- **With `source`**, the lines are cut where that source's lines cross them, or, for a
  polygon source, its polygons' edges: say, a road network split at municipality borders.
  The source lines aren't added.
- **Each piece is a row** with its line's attributes. The pieces are the line's own geometry,
  cut at the crossings, so neighbouring pieces share their end points exactly, and 3D lines
  keep their heights. Multilines are cut part by part.
- **`tolerance`**: without it, lines cut each other only where they meet exactly. Lines
  digitised separately rarely do: a path meant to end on another stops a few millimetres
  short, or overshoots it. With `tolerance`, a line ending within that distance of another
  cuts it at the closest point, and cuts closer together than that are one cut. The line
  that ends near it isn't moved. Set it just above the expected error, e.g. `0.05` for
  data in metres. Requires a projected working CRS.
- Every crossing is cut, including where one line passes over another (a bridge). To keep
  levels apart, split them on separate [branches](#branch), or `filter` them first.

Points and polygons pass through. Use [`combine_lines`](#combine_lines) to join pieces back
into longer lines.

## Keeping other columns

`dissolve` and `combine_lines` turn many features into one, so a column that isn't a `by` key
can have several values where the result has room for one. It's dropped unless an
`aggregates` entry says how to combine them. Give the column (`from`) and `how`:

| `how` | In the editor | Gives | Works on |
|---|---|---|---|
| `any` | one value | One of the values, picked at random. For a column that's the same throughout | anything |
| `most_common` | most common | The value most features have | anything |
| `from_largest` | from the largest | The value of the longest line or the largest area | anything |
| `all` | all of them | Every different value, as text joined with `, ` | anything |
| `sum` | sum | The values added up | numbers |
| `average` | average | The mean | numbers |
| `min` / `max` | lowest / highest | Smallest / largest number, earliest / latest date, first / last alphabetically | numbers, dates, text |
| `count` | count | Without `from`: how many features were combined. With it: how many have a value | anything |

```yaml
aggregates:
  - {from: bruksklasse, how: most_common}        # keeps its name
  - {to: streets, from: LOK.GATENAVN, how: all}  # `to` renames it
  - {to: segments, how: count}
```

- `to` is the column's name after the step. It defaults to the `from` column's.
- **Group by it or aggregate it?** Group by a column when a change in its value should start
  a new feature: two speed limits make two lines. Aggregate it when it shouldn't.
- Empty values (NULL) are skipped. A result is only empty when every value is.
- `any` really is random when the values differ. Use it for columns that are the same
  throughout a group; for the others, group by them or pick another `how`.
- `sum` or `average` on a column that isn't a number is an error that says so, before
  anything runs.

For anything else, write a SQL aggregate in `expr` instead of `from` and `how`:

```yaml
aggregates:
  - {to: length_m, expr: "sum(ST_Length(geom))"}
  - {to: newest_class, expr: "arg_max(bruksklasse, oppdatert)"}
  - {to: all_lit, expr: "bool_and(lit)"}
```

An expression can use any column of the rows going in, but only inside an aggregate function
(`count`, `sum`, `avg`, `min`, `max`, `mode`, `string_agg`, `list`, `any_value`, `arg_max`,
`bool_and`, …). A bare column that isn't a key is an error. Quote a column name with dots or
other special characters: `mode("NET.TYPEVEG")`. In a GeoPackage, prefer `string_agg` to
`list`: GeoPackage has no list type.

## `simplify`

Removes vertices, keeping every line and polygon edge within `tolerance` of the original:
FME's Generalizer, QGIS's Simplify.

```yaml
- type: simplify
  tolerance: 2                # working-CRS units
  shared_boundaries: true     # optional: polygons that fit together edge to edge
  keep_outer_boundary: true   # optional, with shared_boundaries: leave the outer edge as it is
```

- **One feature at a time** (the default): Douglas-Peucker, keeping each shape valid
  (`ST_SimplifyPreserveTopology`). Points pass through.
- **`shared_boundaries: true`** is for polygons that fit together without gaps or overlaps:
  municipalities, land cover, parcels. Simplified one at a time, two neighbours simplify the
  edge they share differently, and gaps and slivers open up between them. With
  `shared_boundaries`, all the polygons are simplified together, so a shared edge is
  simplified once and stays shared (`ST_CoverageSimplify`, Visvalingam-Whyatt). Polygons
  that overlap or nearly fit should go through [`clean_coverage`](#clean_coverage) first.
  Lines and points in the same layer are still simplified one at a time.
- **`keep_outer_boundary: true`** leaves the coverage's outer edge (a national border, a
  coastline) exactly as it is and simplifies only the edges between the polygons.

The two methods measure `tolerance` differently, so the same value removes about as much
detail but not exactly the same vertices. Requires a projected working CRS.

## `snap_to_grid`

Rounds every vertex to a grid: FME's CoordinateRounder, QGIS's Snap points to grid.

```yaml
- type: snap_to_grid
  grid_size: 0.01     # working-CRS units: centimetres in a metric CRS
```

The geometry stays valid: rounding can make a polygon's edges cross, and they are fixed up
(`ST_ReducePrecision`). A polygon narrower than a grid cell collapses to an empty geometry.
Its row is kept; add a [`filter`](#filter) with `NOT ST_IsEmpty(geom)` to drop such rows.
Requires a projected working CRS.

## `clean_coverage`

Makes polygons that should fit together edge to edge (a coverage) fit: digitised separately,
or reprojected, neighbours overlap a little or leave thin gaps.

```yaml
- type: clean_coverage
  snap_distance: 0.05     # optional: snap edges this close together (working-CRS units)
  max_gap_width: 0.5      # optional: fill gaps narrower than this
```

- **Overlaps** are removed: the overlapping area goes to one of the polygons.
- **Edges within `snap_distance`** of each other are snapped together. Without it, a tiny
  distance derived from the data's extent is used, which only catches rounding differences.
- **Gaps narrower than `max_gap_width`** are filled, by giving the area to a neighbour. A gap
  is a hole enclosed by the polygons; an inlet open to the outside isn't one. Without it,
  gaps stay open.

A polygon that lay wholly under another comes out empty, with its row kept. Points and lines
pass through unchanged. Requires a projected working CRS when `snap_distance` or
`max_gap_width` is set.

## `fill_holes`

Removes the holes in polygons: FME's DonutHoleExtractor, QGIS's Delete holes.

```yaml
- type: fill_holes
  max_area: 25    # optional: only holes smaller than this (working-CRS units, e.g. m²)
```

Without `max_area`, every hole is filled. Multipolygons are filled part by part. Points and
lines pass through. Requires a projected working CRS when `max_area` is set.

## `eliminate_slivers`

Merges small polygons into a neighbour: QGIS's Eliminate selected polygons. Slivers left by
an overlay, or areas too small for a map at a smaller scale, go into the polygon next to
them.

```yaml
- type: eliminate_slivers
  max_area: 100                    # polygons smaller than this (working-CRS units, e.g. m²)
  where: "landuse = 'unclassified'"   # optional; with max_area, both must hold
  merge_into: longest_border       # longest_border (default) | largest
```

- **Which polygons**: those smaller than `max_area`, those matching `where`, or with both,
  those matching both. Give at least one.
- **Which neighbour**: of the polygons it shares a border with (a line, not just a corner),
  the one sharing the longest border, or with `merge_into: largest` the largest one. Ties go
  to the first in row order. The neighbour keeps its own attributes and gets the sliver's
  area; the sliver's row is gone.
- **One pass.** A sliver whose neighbours are all slivers too, or that has no neighbours,
  stays as it is.

It works best on polygons that fit together edge to edge: put a
[`clean_coverage`](#clean_coverage) before it when they overlap or nearly fit. Points and
lines pass through. Requires a projected working CRS when `max_area` is set.

## `explode`

Multipart to singleparts: splits multipolygons, multilines and geometry collections into one
row per part, each with the feature's attributes. FME's Deaggregator, QGIS's Multipart to
singleparts.

```yaml
- type: explode
  part_field: part    # optional: number the parts 1, 2, … per feature
```

Collections inside collections are split all the way down. A feature that is already a
single part, or has no geometry, stays one row.

## `filter`

```yaml
- type: filter
  where: "pond IS NOT NULL AND bread_crumbs > 1000"
```

Drops rows where the SQL condition is false (or NULL). With `rejects: <layer>`, those rows are
written to that layer instead of being thrown away. See [`rejects`](#rejects).

## `remove_duplicates`

Keeps the first row of each set of duplicates: FME's DuplicateFilter, QGIS's Delete
duplicate geometries and Delete duplicates by attribute.

```yaml
- type: remove_duplicates
  by: [pond, species]   # columns whose values together make a duplicate
  geometry: true        # optional: compare the geometry too
  rejects: doubles      # optional: write the duplicates to a layer
```

- Give `by`, `geometry: true`, or both: with both, a duplicate has the same values **and**
  the same geometry.
- Geometry is compared as a shape: a polygon whose ring starts at another vertex, or a line
  drawn the other way, is the same geometry. Nearly equal shapes aren't: put a
  [`snap_to_grid`](#snap_to_grid) before it to round off small differences.
- NULLs count as equal: two rows with no value in a `by` column (or no geometry) are
  duplicates there.
- "First" is in row order, as the rows arrive at the step.

## `merge`

Appends another source's rows (`UNION ALL BY NAME`).

```yaml
- type: merge
  source: late_arrivals   # ducks that flew in after the census
```

Columns are matched by name. A column that exists on only one side is NULL on the other.
Merged rows only pass through the steps **after** the merge.

## `snapshot`

Names the chain's current state without changing it.

```yaml
- type: snapshot
  id: after_pond_join
```

Later steps can use `after_pond_join` as a `source:` (join back against processed rows) or as a
`branch:` (keep transforming that copy). With `branch:` set on the snapshot itself, it copies
that branch instead of the main chain.

## `attributes`

Adds, changes and removes columns partway through the pipeline. In the editor's step gallery
it's **Update Attributes**. Use it when a later step needs a value that isn't a column yet: a
key to dissolve or join on, or a value to filter by.

**You only list what you change.** Every column you don't mention is kept as it is and goes on
to the next step, so, unlike in the mapping, there's no need to list them all. Remove one with
`drop`.

```yaml
- type: attributes
  columns:
    - {to: pond_code, expr: "upper(left(pond, 3))"}
    - {to: label, expr: "pond_code || '-' || name"}   # uses the column above
    - {to: bread_crumbs, from: bread_crumbs, cast: INTEGER}   # overwrites it
    - {to: lon, func: lon}
    - {to: size, codelist: {source: species, cases: [{match: Mute swan, value: big}], default: small}}
  drop: [notes]
```

`columns` takes the same rules as the [mapping](mapping.md): `from`, `const`, `expr`, `func`
and `codelist`, each with an optional `cast`. A column with the name of an existing one
replaces it. Otherwise it's added. The columns are set **in order**, so each one can use the
ones set before it. `drop` then removes columns.

A replaced column moves to the end of the row. Column order only matters for the output,
where the mapping sets it. A `func` such as `uuid` or `now` gets one value per feature for
the whole run. A preview may recompute it, though, so it can differ between previews.
`geom` can't be dropped. Setting it with an `expr` replaces the geometry, so the expression
must return one:

```yaml
- type: attributes
  columns:
    - {to: geom, expr: "ST_Boundary(geom)"}   # polygons → their outlines
```

Other conversions work the same way: `ST_ConvexHull(geom)`, `ST_Envelope(geom)`,
`ST_PointOnSurface(geom)`, `ST_BuildArea(geom)` (closed lines → polygons),
`ST_LineInterpolatePoint(geom, 0.5)` (a line's midpoint), … The expression builder's
**Geometry (new shape)** group lists them.

### Keep or drop?

Keep columns by default. The [mapping](mapping.md) chooses what's written, so a column it
doesn't use never reaches the output, and a column you keep is still there if a later step or
the mapping turns out to need it. Drop one when:

- **The mapping is empty.** Then every column is written, and `drop` is how you leave one out.
- **It was only a helper.** A key you made for a join or a flag for a filter can go once it
  has done its job, which keeps previews and column lists tidy.
- **The data is big and the column heavy.** A run stores every column after each step, so
  dropping a long text, JSON or list column you don't need saves memory and time. (A preview
  runs on a small sample, so there it makes little difference.)
- **A join will bring in a column with the same name.** The joined one would come out as
  `name_1`, and `name` would still mean the old column. Drop or rename the old one first.

---

## `branch`

Any step can carry `branch: <snapshot id>` to operate on that branch instead of the main chain:

```yaml
- {type: snapshot, id: personal_space}
- {type: buffer, distance: 50, branch: personal_space}   # only the branch is buffered
- {type: spatial_join, source: personal_space, predicate: within, match: all, fields: {flockmate: name}}
```

The branch must be created by a snapshot **earlier** in the list. Output layers are always
written from the main chain. See [Branches and derived sources](../concepts.md#branches-and-derived-sources).

## `rejects`

`rejects` splits a step's rows in two:
the rows that pass go on down the chain, and the rest are written to an output layer of their
own. It's available on these steps:

| Step | Rejected rows |
|---|---|
| `spatial_join` | base features with no match |
| `attribute_join` | rows with no match |
| `nearest_neighbor` | features with no neighbour (within `max_distance`, if set) |
| `http_lookup` | rows with no hit: a failed request, or no hit in the response |
| `make_points` | rows without both coordinates (as numbers) |
| `clip` | features clip would drop (outside the mask, or only touching it), unclipped |
| `filter` | rows where `where` isn't true (false or NULL) |
| `remove_duplicates` | every duplicate but the first of each set |

```yaml
- type: spatial_join
  source: ponds
  fields: {pond_name: name}
  rejects: ducks_on_land    # unmatched ducks go here, not down the chain with an empty pond_name
- type: filter
  where: "bread_crumbs >= 1000"
  rejects: hungry_ducks     # instead of being discarded
```

- **On a join, `rejects` changes what continues.** Without it, an unmatched row stays in the
  chain with NULL fields. With it, the row leaves the chain and only goes to the rejects layer.
  For `filter`, `clip` and `remove_duplicates`, the rows were dropped anyway, so the other
  layers don't change.
- **The rejects layer is written as the rows are at that step:** every column, without the
  [mapping](mapping.md), and without the join's own fields, which would only be NULL. It goes
  into the config's output file, in the CRS of the pipeline's first output layer.
- **The name must be new.** It can't be the name of an output layer or of another step's
  rejects layer. For a GeoParquet output, each rejects layer is one more file, so a config with
  one layer and one rejects layer writes a folder with two files.
- **Preview them** in the editor by clicking the step's red rejects node in the
  [pipeline flow](../editor/overview.md#ducks-in-a-row-the-pipeline-flow). Each run reports how many rows every
  rejects layer got, in the run log and in the [run history](../editor/preview-and-run.md#run-history).
