# Step types

Steps run in order, each one transforming the rows produced by the step before it (or by the
base source, for the first step). Every step type accepts an optional
[`branch`](#branch) key, and the joins, `clip` and `filter` an optional
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
| Geometry | [`buffer`](#buffer) | same | ✓ | – |
| | [`centroid`](#centroid) | same | ✓ | – |
| | [`clip`](#clip) | may shrink | ✓ | – |
| | [`erase`](#erase) | may shrink | ✓ | – |
| | [`dissolve`](#dissolve) | shrinks | ✓ | drops all but `by` |
| Rows | [`filter`](#filter) | may shrink | – | – |
| | [`merge`](#merge) | grows | – | ✓ (union of columns) |
| | [`snapshot`](#snapshot) | – | – | – |

`fields` on join steps is always a map of **`output_name: source_column`**.

---

## `spatial_join`

Copies attributes from features in another source that spatially match each base feature.

```yaml
- type: spatial_join
  source: ponds
  predicate: intersects        # intersects | contains | within
  match: first                 # first | all
  on_multiple: largest_overlap # first | largest_overlap (only with match: first)
  fields: {pond_name: name, pond_vibe: vibe}
```

| Key | Default | Meaning |
|---|---|---|
| `source` | **required** | Source, derived source or snapshot to join against. |
| `predicate` | `intersects` | Relationship from the base feature's point of view: base *intersects* / *contains* / *is within* the source feature. |
| `match` | `first` | `first`: one row per base feature (NULL fields when nothing matches). `all`: one row per matching pair; base features with no match are kept once, with NULL fields. |
| `on_multiple` | `first` | With `match: first`, which match wins when several do. `first` = the lowest original row order in the source (deterministic). `largest_overlap` = the biggest intersection area (ties fall back to row order). |
| `fields` | `{}` | Columns to copy. |
| `rejects` | – | Layer for the base features with no match, which then leave the chain. See [`rejects`](#rejects). |

!!! warning "`match: first` hides multiple matches"
    A duck paddling exactly on the boundary between two overlapping ponds silently gets only
    one of them. Use
    `on_multiple: largest_overlap` for "which one is it mostly in", or `match: all` to see
    every match.

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
| `fields` | Columns to copy. |
| `rejects` | Layer for the rows with no match, which then leave the chain. See [`rejects`](#rejects). |

Each row picks up at most **one** match. If `right` has duplicate values, which duplicate is
used isn't defined, so make the key unique.

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

Line-on-line overlay, like FME's LineOnLineOverlayer. The running lines are cut where they lie
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
piece gets. Lines that only cross a source line are not cut there. Where several source lines
lie on the same stretch, the first one (in source order) wins. Source lines with no running
line under them are not added.

`tolerance` is how far apart two lines may be and still count as lying on each other. Lines
that look identical rarely are: one dataset was digitised separately, has vertices the other
lacks, or went through a reprojection, and they differ by millimetres. Without a `tolerance`
they must match to within 0.000001 units, so those lines get no source fields, except for a
few centimetres where they cross each other. Set it just above the expected difference, e.g.
`0.05` for data in metres. A cut can land up to the tolerance away from where the source line
ends.

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

Merges geometries that share the same values in the `by` columns.

```yaml
- type: dissolve
  by: [pond, species]   # omit or [] to dissolve everything into one feature
```

**Every column not listed in `by` is dropped.**

## `filter`

```yaml
- type: filter
  where: "pond IS NOT NULL AND bread_crumbs > 1000"
```

Drops rows where the SQL condition is false (or NULL). With `rejects: <layer>`, those rows are
written to that layer instead of being thrown away. See [`rejects`](#rejects).

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

Like the Passed and Failed ports of an FME transformer, `rejects` splits a step's rows in two:
the rows that pass go on down the chain, and the rest are written to an output layer of their
own. It's available on these steps:

| Step | Rejected rows |
|---|---|
| `spatial_join` | base features with no match |
| `attribute_join` | rows with no match |
| `nearest_neighbor` | features with no neighbour (within `max_distance`, if set) |
| `clip` | features clip would drop (outside the mask, or only touching it), unclipped |
| `filter` | rows where `where` isn't true (false or NULL) |

```yaml
- type: spatial_join
  source: ponds
  fields: {pond_name: name}
  rejects: ducks_on_land    # unmatched ducks go here, not on with an empty pond_name
- type: filter
  where: "bread_crumbs >= 1000"
  rejects: hungry_ducks     # instead of being discarded
```

- **On a join, `rejects` changes what continues.** Without it, an unmatched row stays in the
  chain with NULL fields. With it, the row leaves the chain and only goes to the rejects layer.
  For `filter` and `clip`, the rows were dropped anyway, so the other layers don't change.
- **The rejects layer is written as the rows are at that step:** every column, without the
  [mapping](mapping.md), and without the join's own fields, which would only be NULL. It goes
  into the config's output file, in the CRS of the pipeline's first output layer.
- **The name must be new.** It can't be the name of an output layer or of another step's
  rejects layer. For a GeoParquet output, each rejects layer is one more file, so a config with
  one layer and one rejects layer writes a folder with two files.
- **Preview them** in the editor by clicking the step's red rejects node in the
  [pipeline flow](../editor/overview.md#ducks-in-a-row-the-pipeline-flow). Each run reports how many rows every
  rejects layer got, in the run log and in the [run history](../editor/preview-and-run.md#run-history).
