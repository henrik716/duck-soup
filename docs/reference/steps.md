# Step types

Steps run in order, each one transforming the rows produced by the step before it (or by the
base source, for the first step). Every step type accepts an optional
[`branch`](#branch) key.

!!! tip "See them in action"
    [Steps by example](../tutorials/steps.md) runs every step type on a small sample town, with
    before/after diagrams and the actual output.

| `type` | Row count | Changes geometry | Adds columns |
|---|---|---|---|
| [`spatial_join`](#spatial_join) | same (`match: all`: may grow) | – | ✓ |
| [`attribute_join`](#attribute_join) | same | – | ✓ |
| [`nearest_neighbor`](#nearest_neighbor) | same | – | ✓ |
| [`buffer`](#buffer) | same | ✓ | – |
| [`centroid`](#centroid) | same | ✓ | – |
| [`clip`](#clip) | may shrink | ✓ | – |
| [`erase`](#erase) | may shrink | ✓ | – |
| [`dissolve`](#dissolve) | shrinks | ✓ | drops all but `by` |
| [`intersect_overlay`](#intersect_overlay) | may grow or shrink | ✓ | ✓ |
| [`filter`](#filter) | may shrink | – | – |
| [`merge`](#merge) | grows | – | ✓ (union of columns) |
| [`snapshot`](#snapshot) | – | – | – |

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
Features with nothing in range get NULL fields. Needs a projected working CRS when
`max_distance` or `distance_field` is set.

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
overlap any mask (or only touch it) are dropped.

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

## `intersect_overlay`

One output row per overlapping (base × source) pair, with the intersection as the geometry.

```yaml
- type: intersect_overlay
  source: feeding_zones
  fields: {zone: name}
```

Base features that overlap nothing are dropped, and pairs that only touch are ignored. Row
count can grow a lot when there are many overlaps.

## `filter`

```yaml
- type: filter
  where: "pond IS NOT NULL AND bread_crumbs > 1000"
```

Drops rows where the SQL condition is false (or NULL).

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
