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

Type in the search box at the top to find a step: by its name, by a word from its
description, or by the QGIS tool or FME transformer it replaces (`Delete holes`,
`Split with lines`, `Deaggregator`, `DonutHoleExtractor`, …). Words can come in any order. <kbd>Enter</kbd>
adds the first card that matches, and <kbd>↓</kbd> moves to the cards.

The gallery groups the steps the way the [Step types reference](../reference/steps.md) does,
each group with a duck name:

| Gallery group | Gallery card | Step type | In one line |
|---|---|---|---|
| **Paddling together** (joins: add columns from another source) | Spatial Join | `spatial_join` | Copy attributes from intersecting / containing / contained features. |
| | Attribute Join | `attribute_join` | Copy attributes from a row matched on a column value. |
| | Nearest Neighbour | `nearest_neighbor` | Copy attributes from the closest feature, optionally recording the distance. |
| | Intersect Overlay | `intersect_overlay` | One row per overlapping pair, with the intersection as geometry. |
| | Line Overlay | `line_overlay` | Cut lines where another line layer lies on them and copy its attributes, keeping the rest of the network. |
| | HTTP Lookup | `http_lookup` | Call a web API per row (an address search, say) and add values from its JSON response. **Test request** shows the first row's response; click a value to add it as a field. |
| **Shaping up** (geometry: change the shapes) | Make Points | `make_points` | Make point geometry from two coordinate columns, e.g. a geocoding lookup's longitude and latitude. |
| | Buffer Geometry | `buffer` | Grow (or shrink, with a negative distance) each geometry. |
| | Centroid | `centroid` | Replace each geometry with its centre point. |
| | Clip | `clip` | Keep only the part of each feature that overlaps a mask source. |
| | Erase / Difference | `erase` | Cut away the part of each feature that overlaps a mask source. |
| | Dissolve | `dissolve` | Merge features that share the same group-by values, with per-group aggregates. |
| | Combine Lines | `combine_lines` | Join lines that meet end to end into longer lines, one per connected chain with the same group-by values. |
| | Split Lines | `split_lines` | Cut lines where they cross or touch, or where another source's lines or polygon edges cross them. |
| | Simplify / Generalize | `simplify` | Remove vertices within a tolerance, one feature at a time or keeping the boundaries neighbouring polygons share. |
| | Snap to Grid | `snap_to_grid` | Round every vertex to a grid, keeping the geometry valid. |
| | Clean Coverage | `clean_coverage` | Make polygons fit edge to edge: snap nearly matching edges, remove overlaps, fill small gaps. |
| | Fill Holes | `fill_holes` | Remove the holes in polygons, all of them or the ones under an area. |
| | Eliminate Slivers | `eliminate_slivers` | Merge small polygons into the neighbour they share the longest border with. |
| | Multipart to Singleparts | `explode` | Split multipolygons, multilines and collections into a row per part. |
| **Pecking order** (rows: keep some, add more, or set a copy aside) | Filter | `filter` | Drop rows where a SQL condition is false. |
| | Remove Duplicates | `remove_duplicates` | Keep the first row of each set with the same column values and/or geometry. |
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
- **match**: `first` (one row per base feature), `all` (one row per match, spatial join
  only) or `summarize` (one row per base feature, with its matches combined). With `first`, a
  warning under the field says how many features in the sample data match several.
  With `summarize`, each pulled field gets a dropdown for how to combine the matches' values
  (count, all of them, most common, sum, …, or SQL).
- **when several match**: with `match: first`, which match a base feature keeps. `first` takes
  the match that comes first in the join source. `largest_overlap` takes the one with the
  biggest intersection area. Spatial join only, and hidden when **match** is `all`.
- **left / right**: for an attribute join, the upstream column (or a quoted literal such as
  `'Mallard'`) and the column on the join source. A live check under **left** confirms it
  evaluates against real data.
- **pulled fields**: the `source column → output name` pairs this step copies in. **+ field**
  adds one, and **+ all columns** pulls every column of the join source not already pulled. A
  step with no pulled fields still matches, but copies nothing.
- **condition**: for a filter, which rows to keep, as rows of *column · operator · value*
  (is, is not, greater than, contains, is empty, is one of, …), all of which hold or any of
  which does. The **<>** button shows it as SQL, to write anything the rows can't; SQL the
  rows can show switches back to them. It's checked live against sample data.
- **distance**, **max distance**, **distance field**: in working-CRS units.
- **columns reaching this step**: for dissolve and combine lines, every column coming in.
  Group keys are highlighted, kept columns outlined, and the ones the step would drop struck
  through. Click a struck-through column to keep it, or its **by** to group by it.
- **group by**: for dissolve and combine lines, the keys features are grouped on. A key is a
  column, or a SQL expression with a name in **as**, to group by a computed value. A column
  with a name in **as** is written under that name, and still counts as grouped by. Leave the
  list empty to dissolve everything into one feature (or, for combine lines, to join any lines
  that meet).
- **other columns**: for dissolve and combine lines, the columns kept besides the keys, with
  one value per group (per line, for combine lines). Each row is a name, how to combine the
  values (one value, most common, from the largest, all of them, sum, average, lowest,
  highest, count) and the column. Sum and average are only offered for number columns.
  **+ keep all** keeps every dropped column as one value each, and **+ count** adds the
  number of features combined. The **<>** button
  switches a row to SQL, filled in with what it did. The expression builder lists the
  aggregate functions first, and its live check runs the step, so it catches a column used
  outside an aggregate. See [Keeping other columns](../reference/steps.md#keeping-other-columns).
- **only join lines running the same way**: for combine lines, on by default. Untick it to let
  a line be reversed so it joins one running the other way.
- **tolerance**, **grid size**, **snap distance**, **max gap width**: for simplify, snap to
  grid and clean coverage, in working-CRS units.
- **keep shared boundaries shared**: for simplify, simplifies polygons that fit together edge
  to edge as one coverage, so no gaps open up between neighbours. Ticked, it shows **leave the
  outer boundary as it is**. See [`simplify`](../reference/steps.md#simplify).
- **part number column**: for multipart to singleparts, an optional column numbering each
  feature's parts.
- **compare columns**, **compare the geometry**: for remove duplicates, what makes two rows
  duplicates. Give columns, tick the geometry, or both.
- **max area**, **condition**, **merge into**: for eliminate slivers, which polygons go
  (smaller than the area, matching the condition, or both) and which neighbour takes each
  one. **max area** on fill holes fills only the holes smaller than it.
- **cut by**: for split lines, empty to cut the lines where they cross each other, or a
  source to cut them at its lines or polygon edges.
- **Update Attributes** (an attributes step) changes only what you list. Every other column is
  kept as it is and goes on to the next step, as the card says at the top. Its parts:
    - **available here**: the columns that reach this step, grouped by where they come from
      (the base, or an earlier step). Click one to add a row for it, ready to change its value.
      Its **×** adds it to the drop list. Changed columns are outlined, dropped ones struck
      through. **geom**, at the top, replaces the geometry: it adds a row with an expression
      and opens the expression builder, whose **Geometry (new shape)** group has the
      conversions (outline, hull, bounding box, point on surface, …).
    - **add or change columns**: rows like the [mapping](mapping.md)'s: a column name, a rule
      (`from`, `const`, `expr`, `func`, `codelist`) and an optional cast. The name is a
      dropdown of the available columns, so changing one is a pick; type a new name to add a
      column. The rows run top to bottom, so an expression can use a column set in a row above
      it, and its live check sees that column. Drag the grip to reorder.
    - **drop columns**: columns to remove after the ones above are set.
- **expressions**: any SQL field with a pencil icon (a mapping or attribute `expr`, a dissolve
  key or aggregate) opens the [expression builder](mapping.md#expression-builder), which checks
  the expression against the rows that reach this step.
- **rejects → output layer**: on a spatial, attribute or nearest-neighbour join, `clip`, `filter`
  or remove duplicates, a layer for the rows the step rejects (no match, outside the mask,
  condition not true, a duplicate). Those
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
