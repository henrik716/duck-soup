# Output layers

The **output layers** tab of each pipeline lists the layers it writes into the shared
output (the **output file** set on the config card). The YAML side is covered under
[`layers`](../reference/yaml.md#layers) in the reference. Each layer card has:

- **layer name**: the table name inside the GeoPackage (for a multi-layer GeoParquet output,
  the file name).
- **CRS**: the CRS geometry is reprojected to on write.
- **filter**: an optional SQL condition. Rows where it's false are left out of *this* layer
  only. A live check under the field tells you whether it parses.

![The output layers tab](../assets/screenshots/output-layers-light.png#only-light){ .screenshot loading=lazy }
![The output layers tab](../assets/screenshots/output-layers-dark.png#only-dark){ .screenshot loading=lazy }

All layers of a pipeline get the same rows, so filters are how you split them. For example, a
`ducks` layer with no filter and a `ducks_off_pond` layer with `pond IS NULL` (ducks
that wandered off for a waddle).

!!! note "Filters use upstream column names"
    A layer filter runs **before** the mapping, so it refers to the column names coming out of
    the last step (`pond`, `s_pond_type`), not the renamed output columns.

## Per-layer mapping

By default every layer writes the columns from the **mapping** tab. To give one layer
different columns, for example a slim public layer next to a full internal one, tick
**own column mapping for this layer** on its card:

- **+ column** adds a mapping row. The rows work like the ones on the mapping tab: the same
  `from` / `const` / `expr` / `func` / `codelist` kinds, casts and drag-to-reorder.
- **copy pipeline mapping** fills the list with a copy of the mapping tab, so you can start
  from it and remove or change columns. Later changes on the mapping tab don't affect the copy.
- Untick the box to go back to the pipeline mapping. A ticked box with no rows also uses the
  pipeline mapping.

In YAML this is the layer's `mapping:` key. See
[Per-layer mapping](../reference/mapping.md#per-layer-mapping).
