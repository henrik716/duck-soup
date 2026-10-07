# The sample town

The tutorials take place in **Pondsworth**, a small made-up town on the River Waddle where the
ducks run everything. Its data is committed to the repository under
[`data/tutorial/`](https://github.com/henrik716/duck-soup/tree/main/data/tutorial). It's
deliberately tiny (a few dozen features) so you can follow every row from input to output.

--8<-- "docs/tutorials/generated/overview.svg"

<div class="ds-key" markdown>
<span><i class="k-base"></i>places</span>
<span><i class="k-ref"></i>transit stops</span>
<span><i class="k-park"></i>parks</span>
<span><i class="k-water"></i>River Waddle</span>
<span><i class="k-ghost"></i>new places (for `merge`)</span>
</div>

## The datasets

| Source id | File | Geometry | Columns |
|---|---|---|---|
| `districts` | `districts.geojson` | 4 polygons | `district_name`, `ward` (Upstream/Downstream), `population` |
| `places` | `places.geojson` | 11 points | `name`, `category` (a code like `MUS`), `opened` (a year, as text), `bread_crumbs` (handed out per year), `inspected` (dates in mixed formats) |
| `parks` | `parks.geojson` | 4 polygons | `name`, `kind` (park/meadow) |
| `river` | `river.geojson` | 1 polygon | `name` |
| `stops` | `stops.geojson` | 7 points | `stop_name`, `stop_type` (`B` bus, `T` tram, `P` paddle boat) |
| `new_places` | `new_places.geojson` | 2 points | `name`, `category` |
| `categories` | `categories.csv` | none (table) | `code`, `label` |

A few features are there on purpose, to show what happens at the edges:

- The **Old Lighthouse** stands alone outside town, so joins against districts find nothing
  for it.
- The **Old Duck House** has an empty category, and the Old Lighthouse has a code (`LND`) that
  isn't in `categories.csv`.
- **Breadcrumb Hill** has `opened` = `unknown`, and `inspected` mixes `2024-03-14`,
  `14.03.2024` and `March 2024`, which is useful when explaining `cast`.

Every source is GeoJSON in EPSG:4326, like most data you'll download. The examples set
`working_crs: EPSG:32631` (UTM zone 31N), so distances and areas are in metres. Pondsworth
floats in the North Sea in that zone, so it can't be mistaken for a real town.

All examples share the same source list:

```yaml
sources:
  - {id: places,     format: geojson, uri: data/tutorial/places.geojson,     crs: EPSG:4326}
  - {id: districts,  format: geojson, uri: data/tutorial/districts.geojson,  crs: EPSG:4326}
  - {id: parks,      format: geojson, uri: data/tutorial/parks.geojson,      crs: EPSG:4326}
  - {id: river,      format: geojson, uri: data/tutorial/river.geojson,      crs: EPSG:4326}
  - {id: stops,      format: geojson, uri: data/tutorial/stops.geojson,      crs: EPSG:4326}
  - {id: new_places, format: geojson, uri: data/tutorial/new_places.geojson, crs: EPSG:4326}
  - {id: categories, format: csv,     uri: data/tutorial/categories.csv}
```

## Run every example yourself

[`data/tutorial/pondsworth.yaml`](https://github.com/henrik716/duck-soup/blob/main/data/tutorial/pondsworth.yaml)
holds every example from these pages as its own pipeline, each writing one layer named after
the example. From the root of a clone:

```bash
duck-soup run data/tutorial/pondsworth.yaml
```

Then open `output/pondsworth_tutorial.gpkg` in QGIS and compare the layers. To explore them
with live previews instead, start the editor from the repository root, click **import**, and
paste the file's contents.

!!! note "The pages are generated from real runs"
    The result tables and before/after diagrams on these pages come from running the examples
    through duck soup itself ([`scripts/build_tutorial.py`](https://github.com/henrik716/duck-soup/blob/main/scripts/build_tutorial.py)),
    so they show exactly what the engine produces.

## Where to go next

- [Steps by example](steps.md): every step type, with a diagram and its output.
- [Mapping by example](mapping.md): `from`, `const`, `expr`, `func`, `codelist` and `cast`.
