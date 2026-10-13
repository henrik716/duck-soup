# Steps by example

This page runs every step type on the [Pondsworth sample data](index.md) and shows what comes
out. Each example is a pipeline in `pipelines/pondsworth.yaml` (from [`duck-soup tutorial`](index.md#get-the-data)) with the same name as its
section, using the [shared source list](index.md#the-datasets) and
`working_crs: EPSG:32631`. The YAML below shows only the parts that differ.

How to read the diagrams:

<div class="ds-key" markdown>
<span><i class="k-base"></i>base features (the rows flowing through)</span>
<span><i class="k-ref"></i>the other source the step uses</span>
<span><i class="k-miss"></i>a feature with no match</span>
<span><i class="k-ghost"></i>input shown for comparison</span>
</div>

The tables are the actual output, with *NULL* for empty values. In the editor, every step here
is a card in the **add step** gallery; see [Steps in the editor](../editor/steps.md).

## Joins: add columns from another source

### `spatial_join`

**Copies columns from the feature that a base feature touches, contains or lies within.** Use it
to answer "which district is each place in?".

```yaml
--8<-- "docs/tutorials/generated/spatial_join.yaml"
```

--8<-- "docs/tutorials/generated/spatial_join.svg"

After: each place now carries the name of the district it's in. The Old Lighthouse, outside every district, gets none.
{ .ds-caption }

--8<-- "docs/tutorials/generated/spatial_join.md"

- `fields` maps **new column name → column in the joined source**: here, the district's
  `district_name` arrives as `district`.
- Every base feature is kept. The Old Lighthouse is outside every district, so its `district`
  is NULL (the red ring). Add a [`filter`](#filter) afterwards if you only want matches.
- `predicate` is read from the base feature's side: `intersects` (touches or overlaps), `within`
  (base feature inside the source feature), `contains` (base feature contains the source
  feature). For points in polygons, `intersects` and `within` give the same result.

#### When a feature matches several: `on_multiple`

Central Puddle Park overlaps all four districts. By default (`match: first`) a base feature
still gets exactly one match, and `on_multiple` decides which:

```yaml
--8<-- "docs/tutorials/generated/spatial_join_overlap.yaml"
```

--8<-- "docs/tutorials/generated/spatial_join_overlap.svg"

After: the district each option picks for Central Puddle Park. `first` takes Old Puddleton (dashed), `largest_overlap` takes Mallard Quay (filled).
{ .ds-caption }

--8<-- "docs/tutorials/generated/spatial_join_overlap.md"

- `on_multiple: first` (the default) takes the matching district that comes first in the
  source file: Old Puddleton. It's deterministic, but not meaningful.
- `on_multiple: largest_overlap` takes the district with the biggest shared area: most of
  Central Puddle Park is in Mallard Quay.
- In the editor, this is the spatial join's **when several match** field.

#### Keeping every match: `match: all`

```yaml
--8<-- "docs/tutorials/generated/spatial_join_all.yaml"
```

--8<-- "docs/tutorials/generated/spatial_join_all.md"

With `match: all`, a base feature is repeated once per match, so Central Puddle Park appears
four times. The geometry isn't cut: each copy is the whole park. To split it along the district
boundaries, use [`intersect_overlay`](#intersect_overlay) instead.

### `attribute_join`

**Looks up a value in another table by matching columns**, like a spreadsheet VLOOKUP. No
geometry is involved, so the other source can be a plain CSV.

```yaml
--8<-- "docs/tutorials/generated/attribute_join.yaml"
```

--8<-- "docs/tutorials/generated/attribute_join.md"

- A row matches when `left` (evaluated on the base row) equals `right` (a column in the
  lookup source). `left` is a SQL expression, so `upper(category)` or a literal like
  `'MUS'` also work.
- Rows without a match keep NULL: the Old Duck House's category is empty, and `LND` isn't in
  the CSV.
- Each row picks up at most one match. If the lookup column has duplicates, which one is used
  isn't defined, so make it unique.
- Matching is exact, including case. For a single translated column, a
  [file codelist](mapping.md#codelist-from-a-csv-file) is an alternative that can ignore case.

### `nearest_neighbor`

**Copies columns from the closest feature in another source**, whether or not they touch.

```yaml
--8<-- "docs/tutorials/generated/nearest_neighbor.yaml"
```

--8<-- "docs/tutorials/generated/nearest_neighbor.svg"

After: each place is linked to its nearest stop. The Old Lighthouse has no stop within 300 m.
{ .ds-caption }

--8<-- "docs/tutorials/generated/nearest_neighbor.md"

- `distance_field` adds a column with the distance, in working-CRS units (metres here): how far
  each duck has to waddle to its stop.
- `max_distance` limits the search: the Old Lighthouse's nearest stop is over 300 m away, so it
  gets NULL. Setting a radius also makes the step much faster on large data, because without it
  every base feature is compared with every source feature.
- Distances need a projected working CRS; duck soup refuses to measure in degrees.

### `intersect_overlay`

**Cuts base features along the boundaries of another source**, producing one row per
overlapping pair with the shared area as its geometry.

```yaml
--8<-- "docs/tutorials/generated/intersect_overlay.yaml"
```

--8<-- "docs/tutorials/generated/intersect_overlay.svg"

After: Central Puddle Park is cut into four pieces along the district borders. The other parks each lie in one district and stay whole.
{ .ds-caption }

--8<-- "docs/tutorials/generated/intersect_overlay.md"

- Central Puddle Park becomes four pieces, one per district, each carrying that district's
  name. Use this when you need "how much park is in each district?".
- Base features that overlap nothing are dropped, and pairs that only touch along an edge are
  ignored.
- The row count can grow quickly when many features overlap.

### `line_overlay`

**Cuts lines where other lines lie on top of them**, and copies those lines' attributes onto
the shared stretches. Everything else is kept, so the whole network comes out again and can go
into the next `line_overlay`.

Here the footpath network collects attributes from two datasets: the lamp register
(`lit_paths`, which has gaps) and the boardwalk survey (`boardwalks`, just a few stretches).

```yaml
--8<-- "docs/tutorials/generated/line_overlay.yaml"
```

--8<-- "docs/tutorials/generated/line_overlay.svg"

<div class="ds-key" markdown>
<span><i class="k-path"></i>footpaths</span>
<span><i class="k-lamp"></i>lit (`lit_paths`)</span>
<span><i class="k-board"></i>boardwalk (`boardwalks`)</span>
</div>

Each gap in **After** is where one row ends and the next begins: 10 pieces in all. Mill Lane and Waddle Bridge only end on Riverside Walk, so it has no gap there.
{ .ds-caption }

--8<-- "docs/tutorials/generated/line_overlay.md"

- Riverside Walk is lit in the middle, and part of that lit stretch is also a boardwalk, so it
  comes out as five pieces: unlit, lit, lit and oak, lit, unlit. Each piece is its own row, and
  neighbouring pieces meet exactly.
- Every metre of the network is still there: the pieces add up to the original paths.
  The geometry is always the footpath's own; the other two datasets only decide where the cuts
  go and what each piece gets.
- Waddle Bridge ends on Riverside Walk and Reed Way, but they are not cut there: lines that
  only meet or cross don't count as lying on each other.
- `tolerance: 0.1` is needed here. The lamp register and the boardwalk survey were digitised
  separately, and their lines lie a few centimetres off the footpaths. Without a tolerance
  nothing would match, and nothing would get `lamps` or `material`. With it, lines within
  10 cm of each other count as the same line, and a lamp line stopping 3 cm before the end of
  Mill Lane still lights all of it.

## Geometry: change the shapes

### `make_points`

**Makes point geometry from two coordinate columns**, so a table without geometry can be
joined, buffered and mapped like any other layer.

```yaml
--8<-- "docs/tutorials/generated/make_points.yaml"
```

--8<-- "docs/tutorials/generated/make_points.md"

- The sightings come from a CSV with `lon` and `lat` as text. `make_points` casts them to
  numbers and builds the points in `EPSG:4326`, reprojected to the working CRS, so the
  [`spatial_join`](#spatial_join) after it can find each sighting's district.
- The Goosander came without coordinates. It gets no point, and with `rejects` it goes to
  the `unplaced_sightings` layer instead of on down the chain.
- A CSV whose coordinates are already numbers doesn't need this step: set `x_field` and
  `y_field` on the source. `make_points` is for coordinates that turn up partway through
  a pipeline, say from an [`http_lookup`](../reference/steps.md#http_lookup) geocoder.

### `buffer`

**Grows each geometry by a distance** (or shrinks polygons, with a negative distance).

```yaml
--8<-- "docs/tutorials/generated/buffer.yaml"
```

--8<-- "docs/tutorials/generated/buffer.svg"

After: each stop is a 150 m circle.
{ .ds-caption }

- The distance is in working-CRS units: 150 m here, roughly how far a duck will waddle for a
  bus. Like other distance operations, it needs a projected working CRS.
- Points become circles and lines become corridors. Attributes are unchanged.
- To buffer a *copy* of the data and keep the originals, use a [snapshot branch](#snapshot) or a
  [derived source](../concepts.md#branches-and-derived-sources) with `buffer:`.

### `centroid`

**Replaces each geometry with its centre point.**

```yaml
--8<-- "docs/tutorials/generated/centroid.yaml"
```

--8<-- "docs/tutorials/generated/centroid.svg"

After: each park is replaced by its centre point. The dashed outlines are the parks they came from.
{ .ds-caption }

Useful for labelling, or for joining polygons to other polygons by "where is its middle?"
instead of by overlap. For a curved or C-shaped polygon, the centroid can fall outside the
shape itself.

### `clip`

**Keeps only the part of each feature that lies inside a mask.**

```yaml
--8<-- "docs/tutorials/generated/clip.yaml"
```

--8<-- "docs/tutorials/generated/clip.svg"

After: only the corner of Central Puddle Park inside Old Puddleton (teal, dashed) is left. The dashed grey parks are dropped.
{ .ds-caption }

--8<-- "docs/tutorials/generated/clip.md"

- Here the mask is a [derived source](../concepts.md#branches-and-derived-sources): the
  `districts` source filtered to Old Puddleton, created without adding a step to the main
  chain.
- Only Central Puddle Park reaches into Old Puddleton, so only that corner of it survives.
  Features outside the mask are dropped entirely.
- If a feature overlaps several mask features, it's clipped by the first one it matches. To
  clip to an area made of several polygons, use a mask that is a single feature: filter it down
  to one (as here), or [`dissolve`](#dissolve) it in a separate pipeline and use that output
  layer as the mask source.

### `erase`

**Cuts away the part of each feature that lies inside another source.** It's the opposite of
`clip`.

```yaml
--8<-- "docs/tutorials/generated/erase.yaml"
```

--8<-- "docs/tutorials/generated/erase.svg"

After: the river's strip is cut out of Central Puddle Park, leaving it in two parts. The other parks don't touch the river.
{ .ds-caption }

--8<-- "docs/tutorials/generated/erase.md"

- The River Waddle runs through Central Puddle Park, so the park loses that strip (its area
  drops from about 41,800 m² to 28,600 m², the missing third was river all along) and may become a
  multi-part polygon.
- Features that don't touch the river pass through unchanged. A feature erased completely
  is dropped.
- Unlike `clip`, `erase` subtracts *all* the matching features at once.

### `dissolve`

**Merges features that share the same values**, combining their geometries into one.

```yaml
--8<-- "docs/tutorials/generated/dissolve.yaml"
```

--8<-- "docs/tutorials/generated/dissolve.svg"

After: the four districts are merged into their two wards.
{ .ds-caption }

--8<-- "docs/tutorials/generated/dissolve.md"

- The four districts become the two wards, Upstream and Downstream. Leave `by` out to merge
  everything into a single feature (a town boundary, for example).
- **Only the `by` keys and the `aggregates` are kept**, since the other columns have no
  single value per ward. An aggregate says how to combine them: `how: sum` adds up the
  districts' populations, and `how: all` lists their names. `most_common`, `average`, `min`,
  `max`, `count` and others work the same way, and so does a SQL aggregate in `expr`. See
  [Keeping other columns](../reference/steps.md#keeping-other-columns).
- A key can also be a SQL expression with a name, such as
  `{to: initial, expr: "left(district_name, 1)"}`, to group by a computed value.

### `combine_lines`

**Joins lines that meet end to end** into longer lines, one per connected chain with the same
values.

```yaml
--8<-- "docs/tutorials/generated/combine_lines.yaml"
```

--8<-- "docs/tutorials/generated/combine_lines.svg"

**Before** is what the step gets: the 10 pieces of the two `line_overlay` steps, a gap between each. **After**, pieces of the same path with the same lighting that meet end to end are one line again: 7 lines.
{ .ds-caption }

--8<-- "docs/tutorials/generated/combine_lines.md"

- The two [`line_overlay`](#line_overlay) steps cut the paths into 10 pieces, by lighting and
  by boardwalk. Combining by `name` and `lamps` rejoins the pieces only the boardwalks
  split, giving 7 lines. The lit stretch of Riverside Walk is one line again, made of 3
  pieces.
- Riverside Walk's two unlit stretches stay two features: they don't touch. `dissolve` would
  have merged them into one multi-part line.
- The `aggregates` are computed per chain, so `pieces` counts the pieces in each line, and
  `materials` lists the boardwalks it crosses.
- Lines are joined only where one ends and the next begins, the way they run. Set
  `preserve_direction: false` to let a line be reversed to fit.

### `split_lines`

**Cuts lines where they cross or touch each other**, so every junction becomes an end point:
the noding a network needs before routing or counting segments.

```yaml
--8<-- "docs/tutorials/generated/split_lines.yaml"
```

--8<-- "docs/tutorials/generated/split_lines.svg"

Each gap in **After** is a cut: Riverside Walk where Mill Lane and Waddle Bridge meet it, and Reed Way where the bridge ends on it.
{ .ds-caption }

--8<-- "docs/tutorials/generated/split_lines.md"

- Mill Lane ends on Riverside Walk, and Waddle Bridge starts on it, so Riverside Walk
  is cut in two places and comes out as 3 pieces. The lines that only end there aren't cut.
- Waddle Bridge was meant to end on Reed Way, but after the trip through longitude and
  latitude its end misses by millimetres, as separately digitised lines usually do.
  `tolerance: 0.1` cuts Reed Way there anyway; without it, Reed Way stays in one piece.
- Each piece keeps its line's attributes, and the pieces are the line's own geometry, so
  neighbouring pieces share their end points exactly. Give `source` to cut at another
  layer's lines or polygon edges instead (paths split at district borders, say).

### `clean_coverage`

**Makes polygons that should fit edge to edge actually fit.** The reed beds were surveyed
zone by zone: Sedge Shallows reaches 1.2 m into Cattail Corner, and Cattail Corner stops up
to a metre short of Reedy Reach, leaving a thin gap between them.

```yaml
--8<-- "docs/tutorials/generated/clean_coverage.yaml"
```

--8<-- "docs/tutorials/generated/clean_coverage.md"

- The overlap is gone (`overlap_m2` is the area each zone shares with the others), and
  the gap, narrower than `max_gap_width`, is filled. The three zones now cover their
  57 600 m² exactly once.
- A gap is a hole enclosed by the polygons; an inlet open to the outside isn't filled.
  `snap_distance` also snaps edges that are nearly, but not exactly, on top of each other.

### `simplify`

**Removes vertices**, keeping every edge within `tolerance` of the original.

```yaml
--8<-- "docs/tutorials/generated/simplify.yaml"
```

--8<-- "docs/tutorials/generated/simplify.svg"

The dots are the vertices. After simplifying, each wiggly edge keeps a fraction of them, and the edge two zones share is still a single edge.
{ .ds-caption }

--8<-- "docs/tutorials/generated/simplify.md"

- The reed beds' wiggly edges have a vertex every 5 metres. Simplified with a 6 m
  tolerance, each zone keeps a fraction of them.
- `shared_boundaries: true` simplifies the zones together, so an edge two of them share
  is simplified once and stays shared: the areas still add up to 57 600 m², with no gaps or
  slivers between the zones. Simplified one at a time, the two sides of a shared edge
  would come out differently.
- Shared-boundary simplification needs polygons that don't overlap, which is why
  [`clean_coverage`](#clean_coverage) runs first. `keep_outer_boundary: true` would leave
  the outer edge of the reed beds as it is.

### `snap_to_grid`

**Rounds every vertex to a grid**, here a coarse 50 m one so the jump is visible.

```yaml
--8<-- "docs/tutorials/generated/snap_to_grid.yaml"
```

--8<-- "docs/tutorials/generated/snap_to_grid.svg"

After: each place has moved to the nearest point of a 50 m grid. The grey dots are where they were.
{ .ds-caption }

--8<-- "docs/tutorials/generated/snap_to_grid.md"

- Each place moves to the nearest grid point; the grey dots are where they were. A grid of
  `0.01` (centimetres) is the usual choice, to drop the spurious decimals a reprojection
  leaves behind.
- The geometry stays valid. A polygon narrower than a grid cell collapses to an empty
  geometry, and its row is kept.

### `fill_holes`

**Removes the holes in polygons**, or with `max_area` only the small ones.

```yaml
--8<-- "docs/tutorials/generated/fill_holes.yaml"
```

--8<-- "docs/tutorials/generated/fill_holes.svg"

**Before**: the parks (teal) are erased from the districts. **After**: the small hole in Featherhill is filled (dashed); the two larger ones stay. The square in the middle isn't a hole: Central Puddle Park took a corner off each of four districts.
{ .ds-caption }

--8<-- "docs/tutorials/generated/fill_holes.md"

- Erasing the parks punches a hole in three districts (Central Puddle Park, on the
  corner, only takes a bite out of the edges). With `max_area: 30000`, Willow Wallow's
  22 400 m² hole in Featherhill is filled; Kingfisher Meadow's and Bulrush Fields' are
  larger and stay.
- Without `max_area`, every hole is filled.

### `eliminate_slivers`

**Merges small polygons into a neighbour.** Overlaying the parks with the districts cuts
Central Puddle Park, which straddles the town centre, into four pieces; the one in
Featherhill (5 600 m², teal below) is too small to keep.

```yaml
--8<-- "docs/tutorials/generated/eliminate_slivers.yaml"
```

--8<-- "docs/tutorials/generated/eliminate_slivers.svg"

**Before**: the pieces the overlay cuts the parks into, the sliver in teal. **After**: it's part of the Old Puddleton piece above it.
{ .ds-caption }

--8<-- "docs/tutorials/generated/eliminate_slivers.md"

- The sliver merges into the neighbour it shares the longest border with: the Old
  Puddleton piece, which grows from 9 600 to 15 200 m². The sliver's row is gone; the
  neighbour keeps its attributes. `merge_into: largest` picks the largest neighbour
  instead.
- `where` picks the polygons by a condition, alone or together with `max_area`. A sliver
  with no neighbour to merge into stays as it is.

### `explode`

**Splits multipart features into a row per part.**

```yaml
--8<-- "docs/tutorials/generated/explode.yaml"
```

--8<-- "docs/tutorials/generated/explode.md"

- The [`dissolve`](#dissolve) by `kind` makes two multipolygons: the two meadows and the
  two parks, none of them touching. `explode` turns each into its parts again, with the
  feature's attributes, and `part_field` numbers them.
- A feature that is already a single part stays one row.

## Rows: keep some, add more, or set a copy aside

### `filter`

**Drops the rows where a SQL condition is false.**

```yaml
--8<-- "docs/tutorials/generated/filter.yaml"
```

--8<-- "docs/tutorials/generated/filter.svg"

After: the places that pass the condition. The grey ones are dropped.
{ .ds-caption }

--8<-- "docs/tutorials/generated/filter.md"

- The condition is any DuckDB SQL expression that returns true/false, using the columns
  available at that point in the chain, including ones pulled in by earlier steps (`district`
  here).
- Rows where the condition is NULL are dropped too. Nobody has counted the crumbs at the
  Duckling Swim School, so `bread_crumbs >= 20000` is NULL rather than false, and the row goes.
- To split rows into several outputs rather than drop them, keep them all and give each
  [output layer](../editor/output-layers.md) its own `filter`.

### `remove_duplicates`

**Keeps the first row of each set of duplicates.**

```yaml
--8<-- "docs/tutorials/generated/remove_duplicates.yaml"
```

--8<-- "docs/tutorials/generated/remove_duplicates.md"

- One place per category: the first in row order. The four others (two more cafés, a
  second library and the Museum of Rubber Ducks) go to the `second_in_category` layer.
- `geometry: true` compares the shapes instead of, or as well as, the columns: two
  features with the same geometry are duplicates whatever vertex they start at.

### `merge`

**Appends the rows of another source** to the chain.

```yaml
--8<-- "docs/tutorials/generated/merge.yaml"
```

--8<-- "docs/tutorials/generated/merge.svg"

**Before**: the two new places (teal) and the chain's own. **After**: all of them are rows of the chain.
{ .ds-caption }

--8<-- "docs/tutorials/generated/merge.md"

- Columns are matched by name. `new_places` has no `opened` or `bread_crumbs`, so those are
  NULL for the two new rows. A column only the new source has is added, NULL for the existing
  rows.
- Merged rows only go through the steps that come **after** the merge. Put the merge first if
  everything should be joined and filtered the same way.

### `snapshot`

**Names the chain's current state** so later steps can join against it, or keep working on a
copy of it (a *branch*) while the main chain carries on.

This example finds the places within 250 m of a crumb hotspot (a place handing out 50,000+
bread crumbs a year), where the ducks are bound to gather:

```yaml
--8<-- "docs/tutorials/generated/snapshot.yaml"
```

--8<-- "docs/tutorials/generated/snapshot.svg"

After: the two crumb hotspots' 250 m circles. A place inside another hotspot's circle gets `near_hotspot`; the red ones have no hotspot nearby.
{ .ds-caption }

--8<-- "docs/tutorials/generated/snapshot.md"

What happens, step by step:

1. `snapshot` copies the places into a branch called `crumb_hotspots`. The main chain is
   unchanged.
2. `filter` and `buffer` with `branch: crumb_hotspots` reduce the copy to the two hotspots and
   turn them into 250 m circles. The main chain still holds all eleven places as points.
3. The `spatial_join` on the main chain uses `source: crumb_hotspots`, so each place is checked
   against the circles. `match: all` keeps one row per circle a place falls in.
4. The last `filter` drops each hotspot's match with its own circle.

Output layers are always written from the main chain. A branch is only useful as the `source`
of a later step.

## Attributes: set columns between steps

### `attributes`

**Adds, overwrites or removes columns** partway through the chain, for a later step to use.

```yaml
--8<-- "docs/tutorials/generated/attributes.yaml"
```

--8<-- "docs/tutorials/generated/attributes.md"

- `columns` takes the same rules as the [mapping](mapping.md). `opened` is text in the
  source, and the first column overwrites it with a number (`cast: INTEGER`).
- The columns are set in order, so `era` can use the `opened` value set just above it.
- The `filter` after it can then use `era`, a column that doesn't exist in the source.
  Without the attributes step, the filter would have to repeat the whole `CASE` expression.
- You only list what you change. Every column you don't mention (`name`, `category`,
  `bread_crumbs`, …) is kept as it is and goes on to the next step. `drop` removes the ones
  you don't want, here `inspected`.

