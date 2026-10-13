# Steps

The **steps** tab holds the ordered list of operations applied to the base features. The full
list, with every option, is in the [Step types reference](../reference/steps.md). This page
covers how to work with steps in the editor. For what each step does, with diagrams, see
[Steps by example](../tutorials/steps.md).

## Adding a step

Click **add step** (at the top or bottom of the list) to open the step gallery. To insert a
step somewhere in the middle, click the **+** on that connection in the
[pipeline flow](overview.md#ducks-in-a-row-the-pipeline-flow) instead:

![The step gallery](../assets/screenshots/step-gallery-light.png#only-light){ .screenshot .narrow loading=lazy }
![The step gallery](../assets/screenshots/step-gallery-dark.png#only-dark){ .screenshot .narrow loading=lazy }

The gallery groups the steps the way the [Step types reference](../reference/steps.md) does,
each group with a duck name:

| Gallery group | Gallery card | Step type | In one line |
|---|---|---|---|
| **Paddling together** (joins: add columns from another source) | Spatial Join | `spatial_join` | Copy attributes from intersecting / containing / contained features. |
| | Attribute Join | `attribute_join` | Copy attributes from a row matched on a column value. |
| | Nearest Neighbour | `nearest_neighbor` | Copy attributes from the closest feature, optionally recording the distance. |
| | Intersect Overlay | `intersect_overlay` | One row per overlapping pair, with the intersection as geometry. |
| | Line Overlay | `line_overlay` | Cut lines where another line layer lies on them and copy its attributes, keeping the rest of the network. |
| **Shaping up** (geometry: change the shapes) | Buffer Geometry | `buffer` | Grow (or shrink, with a negative distance) each geometry. |
| | Centroid | `centroid` | Replace each geometry with its centre point. |
| | Clip | `clip` | Keep only the part of each feature that overlaps a mask source. |
| | Erase / Difference | `erase` | Cut away the part of each feature that overlaps a mask source. |
| | Dissolve | `dissolve` | Merge features that share the same group-by values, with per-group aggregates. |
| **Pecking order** (rows: keep some, add more, or set a copy aside) | Filter | `filter` | Drop rows where a SQL condition is false. |
| | Merge / Union | `merge` | Append another source's rows, matched by column name. |
| | Snapshot | `snapshot` | Name the current state so later steps can join back against it. |
| **Ruffling feathers** (attributes: calculate, overwrite or drop columns) | Update Attributes | `attributes` | Add, overwrite or remove columns partway through the pipeline. |

## The step card

![The steps tab with an expanded spatial join](../assets/screenshots/steps-tab-light.png#only-light){ .screenshot loading=lazy }
![The steps tab with an expanded spatial join](../assets/screenshots/steps-tab-dark.png#only-dark){ .screenshot loading=lazy }

The header shows the step's type and a short summary, such as `→ ponds` for a join source,
`(50m)` for a buffer, `by pond · Σ 2` for a dissolve with two aggregates, `+3 · −1` for an
attributes step that sets three columns and drops one, or `@personal_space` when the step runs
on a branch. Hover the type to see what the step does. The header buttons are:

- :material-eye: **Preview up to this step** shows the rows exactly as they are after this
  step, on the map and in the table, before the mapping is applied. The card is highlighted
  while you're previewing it, and a banner above the table offers **Show pipeline output** to
  switch back.
- :material-code-tags: **Show the SQL** opens the [SQL tab](preview-and-run.md#sql-tab) at
  this step.
- **↑ / ↓** move the step. You can also drag the grip handle, or press
  <kbd>Alt</kbd>+<kbd>↑</kbd>/<kbd>↓</kbd> while it has focus.
- **remove** deletes the step. Undo with <kbd>Ctrl</kbd>+<kbd>Z</kbd>.

The body depends on the step type:

- **source**: which source, derived source or snapshot to join against or use as a mask.
- **predicate**: `intersects`, `contains` or `within` (spatial join, clip, erase).
- **match**: `first` (one row per base feature) or `all` (one row per match). Spatial join only.
- **when several match**: with `match: first`, which match a base feature keeps. `first` takes
  the match that comes first in the join source. `largest_overlap` takes the one with the
  biggest intersection area. Spatial join only, and hidden when **match** is `all`.
- **left / right**: for an attribute join, the upstream column (or a quoted literal such as
  `'Mallard'`) and the column on the join source. A live check under **left** confirms it
  evaluates against real data.
- **pulled fields**: the `source column → output name` pairs this step copies in. **+ field**
  adds one, and **+ all columns** pulls every column of the join source not already pulled. A
  step with no pulled fields still matches, but copies nothing.
- **where**: for a filter, a SQL boolean such as `pond IS NOT NULL`, checked live against
  sample data.
- **distance**, **max distance**, **distance field**: in working-CRS units.
- **group by**: for dissolve, the keys features are grouped on. A key is a column, or a SQL
  expression with a name in **as**, to group by a computed value. Leave the list empty to
  dissolve everything into one feature.
- **aggregates**: for dissolve, columns computed per group: a name, and a SQL aggregate such as
  `sum(population)` or `string_agg(name, ', ')`. The expression builder (the pencil icon) lists
  the aggregate functions first. Its live check runs the dissolve, so it catches a column used
  outside an aggregate. Only the keys and aggregates are kept. Every other column is dropped.
- **Update Attributes** (an attributes step) changes only what you list. Every other column is
  kept as it is and goes on to the next step, as the card says at the top. Its parts:
    - **available here**: the columns that reach this step, grouped by where they come from
      (the base, or an earlier step). Click one to add a row for it, ready to change its value.
      Its **×** adds it to the drop list. Changed columns are outlined, dropped ones struck
      through.
    - **add or change columns**: rows like the [mapping](mapping.md)'s: a column name, a rule
      (`from`, `const`, `expr`, `func`, `codelist`) and an optional cast. The name is a
      dropdown of the available columns, so changing one is a pick; type a new name to add a
      column. The rows run top to bottom, so an expression can use a column set in a row above
      it, and its live check sees that column. Drag the grip to reorder.
    - **drop columns**: columns to remove after the ones above are set.
- **expressions**: any SQL field with a pencil icon (a mapping or attribute `expr`, a dissolve
  key or aggregate) opens the [expression builder](mapping.md#expression-builder), which checks
  the expression against the rows that reach this step.
- **rejects → output layer**: on a spatial, attribute or nearest-neighbour join, `clip` or `filter`, a
  layer for the rows the step rejects (no match, outside the mask, condition not true). Those
  rows then go there instead of continuing down the chain or being discarded. See [`rejects`](../reference/steps.md#rejects).
- **apply to branch**: run this step on a named [snapshot branch](../concepts.md#branches-and-derived-sources)
  instead of the main chain. For a snapshot step this reads **snapshot from branch**.

Column dropdowns and checks in a step card offer only the columns that reach that step: the
base source's, plus what earlier steps add, minus what they drop or dissolve away. A column
an earlier step drops isn't offered, and neither is one a later step adds.

!!! tip "Keep columns, or drop them?"
    Keep them by default. The [mapping](mapping.md) chooses what's written, so a column it
    doesn't use never reaches the output, and a column you keep is still there if you need it
    later. Drop one in **Update Attributes** when:

    - **the mapping is empty**: then every column is written, and dropping is how you leave
      one out.
    - **it was only a helper**, such as a key you made for a join or a flag for a filter, and
      it has done its job.
    - **the data is big and the column heavy** (long text, JSON, lists) and you don't need it:
      a run stores every column after each step, so dropping it early saves memory and time.
    - **a join will bring in a column with the same name**: the joined one would come out as
      `name_1`, and `name` would still mean the old column. Drop (or rename) the old one first.

    See [Keep or drop?](../reference/steps.md#keep-or-drop) in the step reference.

## Working step by step

![Previewing the rows after step 1](../assets/screenshots/step-preview-light.png#only-light){ .screenshot .narrow loading=lazy }
![Previewing the rows after step 1](../assets/screenshots/step-preview-dark.png#only-dark){ .screenshot .narrow loading=lazy }

The step preview is the main tool for debugging a pipeline. A typical loop:

1. Add a join step and pull a field or two.
2. Preview up to the step and sort the table by the pulled column. A lot of empty values
   usually means a CRS problem or the wrong predicate.
3. Set the join's **rejects** to send unmatched rows to a layer of their own, add a `filter`
   step (`pulled_column IS NOT NULL`) if you only want matched rows, or keep them all and split
   matched/unmatched into separate [output layers](output-layers.md).
4. **count rows** (sample) in the pipeline flow shows the row count after every step at
   once, so a join that matches far fewer rows than expected stands out.

When a step's preview draws a buffer or a nearest-neighbour search radius, the map shows it as
an overlay so you can check the distance visually.
