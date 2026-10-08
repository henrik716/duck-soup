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

--8<-- "docs/tutorials/generated/intersect_overlay.md"

- Central Puddle Park becomes four pieces, one per district, each carrying that district's
  name. Use this when you need "how much park is in each district?".
- Base features that overlap nothing are dropped, and pairs that only touch along an edge are
  ignored.
- The row count can grow quickly when many features overlap.

## Geometry: change the shapes

### `buffer`

**Grows each geometry by a distance** (or shrinks polygons, with a negative distance).

```yaml
--8<-- "docs/tutorials/generated/buffer.yaml"
```

--8<-- "docs/tutorials/generated/buffer.svg"

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

Useful for labelling, or for joining polygons to other polygons by "where is its middle?"
instead of by overlap. For a curved or C-shaped polygon, the centroid can fall outside the
shape itself.

### `clip`

**Keeps only the part of each feature that lies inside a mask.**

```yaml
--8<-- "docs/tutorials/generated/clip.yaml"
```

--8<-- "docs/tutorials/generated/clip.svg"

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

--8<-- "docs/tutorials/generated/erase.md"

- The River Waddle runs through Central Puddle Park, so the park loses that strip (its area
  drops from about 41,800 m² to 28,600 m², the rest being, well, puddle) and may become a
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

--8<-- "docs/tutorials/generated/dissolve.md"

- The four districts become the two wards, Upstream and Downstream. Leave `by` out to merge
  everything into a single feature (a town boundary, for example).
- **Every column not listed in `by` is dropped**, since there's no single value to keep for
  it. Join the attributes back afterwards if you need them.

## Rows: choose which rows flow on

### `filter`

**Drops the rows where a SQL condition is false.**

```yaml
--8<-- "docs/tutorials/generated/filter.yaml"
```

--8<-- "docs/tutorials/generated/filter.svg"

--8<-- "docs/tutorials/generated/filter.md"

- The condition is any DuckDB SQL expression that returns true/false, using the columns
  available at that point in the chain, including ones pulled in by earlier steps (`district`
  here).
- Rows where the condition is NULL are dropped too. Nobody has counted the crumbs at the
  Duckling Swim School, so `bread_crumbs >= 20000` is NULL rather than false, and the row goes.
- To split rows into several outputs rather than drop them, keep them all and give each
  [output layer](../editor/preview-and-run.md#output-layers) its own `filter`.

### `merge`

**Appends the rows of another source** to the chain.

```yaml
--8<-- "docs/tutorials/generated/merge.yaml"
```

--8<-- "docs/tutorials/generated/merge.svg"

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
