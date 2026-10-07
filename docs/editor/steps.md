# Steps

The **steps** tab holds the ordered list of operations applied to the base features. The full
list, with every option, is in the [Step types reference](../reference/steps.md). This page
covers how to work with steps in the editor.

## Adding a step

Click **add step** (at the top or bottom of the list) to open the step gallery:

![The step gallery](../assets/screenshots/step-gallery-light.png#only-light){ .screenshot .narrow loading=lazy }
![The step gallery](../assets/screenshots/step-gallery-dark.png#only-dark){ .screenshot .narrow loading=lazy }

| Gallery card | Step type | In one line |
|---|---|---|
| Spatial Join | `spatial_join` | Copy attributes from intersecting / containing / contained features. |
| Attribute Join | `attribute_join` | Copy attributes from a row matched on a column value. |
| Nearest Neighbour | `nearest_neighbor` | Copy attributes from the closest feature, optionally recording the distance. |
| Buffer Geometry | `buffer` | Grow (or shrink, with a negative distance) each geometry. |
| Centroid | `centroid` | Replace each geometry with its centre point. |
| Clip | `clip` | Keep only the part of each feature that overlaps a mask source. |
| Erase / Difference | `erase` | Cut away the part of each feature that overlaps a mask source. |
| Dissolve | `dissolve` | Merge features that share the same group-by values. |
| Intersect Overlay | `intersect_overlay` | One row per overlapping pair, with the intersection as geometry. |
| Filter | `filter` | Drop rows where a SQL condition is false. |
| Merge / Union | `merge` | Append another source's rows, matched by column name. |
| Snapshot | `snapshot` | Name the current state so later steps can join back against it. |

## The step card

![The steps tab with an expanded spatial join](../assets/screenshots/steps-tab-light.png#only-light){ .screenshot loading=lazy }
![The steps tab with an expanded spatial join](../assets/screenshots/steps-tab-dark.png#only-dark){ .screenshot loading=lazy }

The header shows the step's type and a short summary, such as `→ fylke` for a join source,
`(500m)` for a buffer, or `@zones` when the step runs on a branch. Hover the type to see what the step does. The header buttons are:

- :material-eye: **Preview up to this step** shows the rows exactly as they are after this
  step, on the map and in the table, before the mapping is applied. The card is highlighted
  while you're previewing it, and a banner above the table offers **Show pipeline output** to
  switch back.
- **↑ / ↓** move the step. You can also drag the grip handle, or press
  <kbd>Alt</kbd>+<kbd>↑</kbd>/<kbd>↓</kbd> while it has focus.
- **remove** deletes the step. Undo with <kbd>Ctrl</kbd>+<kbd>Z</kbd>.

The body depends on the step type:

- **source**: which source, derived source or snapshot to join against or use as a mask.
- **predicate**: `intersects`, `contains` or `within` (spatial join, clip, erase).
- **match**: `first` (one row per base feature) or `all` (one row per match). Spatial join only.
- **left / right**: for an attribute join, the upstream column (or a quoted literal such as
  `'Embassies'`) and the column on the join source. A live check under **left** confirms it
  evaluates against real data.
- **pulled fields**: the `source column → output name` pairs this step copies in. **+ field**
  adds one, and **+ all columns** pulls every column of the join source not already pulled. A
  step with no pulled fields still matches, but copies nothing.
- **where**: for a filter, a SQL boolean such as `county IS NOT NULL`, checked live against
  sample data.
- **distance**, **max distance**, **distance field**: in working-CRS units.
- **group by columns**: for dissolve. Leave it empty to dissolve everything into one feature.
  Every column not listed is dropped.
- **apply to branch**: run this step on a named [snapshot branch](../concepts.md#branches-and-derived-sources)
  instead of the main chain. For a snapshot step this reads **snapshot from branch**.

!!! info "`on_multiple` is YAML-only"
    The spatial join's `on_multiple: largest_overlap` option (pick the match with the biggest
    overlap, instead of the first one) isn't in the editor yet. Set it in the YAML. See
    [Spatial join](../reference/steps.md#spatial_join).

## Working step by step

![Previewing the rows after step 1](../assets/screenshots/step-preview-light.png#only-light){ .screenshot .narrow loading=lazy }
![Previewing the rows after step 1](../assets/screenshots/step-preview-dark.png#only-dark){ .screenshot .narrow loading=lazy }

The step preview is the main tool for debugging a pipeline. A typical loop:

1. Add a join step and pull a field or two.
2. Preview up to the step and sort the table by the pulled column. A lot of empty values
   usually means a CRS problem or the wrong predicate.
3. Add a `filter` step (`pulled_column IS NOT NULL`) if you only want matched rows, or keep
   them all and split matched/unmatched into separate
   [output layers](preview-and-run.md#output-layers).

When a step's preview draws a buffer or a nearest-neighbour search radius, the map shows it as
an overlay so you can check the distance visually.
