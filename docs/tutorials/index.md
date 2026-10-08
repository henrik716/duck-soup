# The sample town

The tutorials take place in **Pondsworth**, a small made-up town on the River Waddle where the
ducks run everything. Its data comes with duck soup, so `duck-soup tutorial` puts it on your
machine (see [Get the data](#get-the-data)). It's deliberately tiny (a few dozen features) so you
can follow every row from input to output.

--8<-- "docs/tutorials/generated/overview.svg"

<div class="ds-key" markdown>
<span><i class="k-base"></i>places</span>
<span><i class="k-ref"></i>transit stops</span>
<span><i class="k-park"></i>parks</span>
<span><i class="k-water"></i>River Waddle</span>
<span><i class="k-trail"></i>footpaths</span>
<span><i class="k-ghost"></i>new places (for `merge`)</span>
</div>

## Get the data

One command copies the Pondsworth data and a config with every tutorial example into a folder:
`data/tutorial/` holds the datasets, and `pipelines/pondsworth.yaml` the config. Run it in the
editor's project folder (by default `~/duck-soup`), so the editor finds the config too:

=== "pipx / uv / pip"

    ```bash
    mkdir -p ~/duck-soup      # the editor's default project folder
    cd ~/duck-soup
    duck-soup tutorial
    duck-soup serve           # then pick "pondsworth" in the editor's load list
    ```

=== "Docker"

    From the folder you mount as `/data` (your project folder):

    ```bash
    docker run --rm -v "$PWD":/data ghcr.io/henrik716/duck-soup duck-soup tutorial
    docker run -p 8000:8000 -v "$PWD":/data ghcr.io/henrik716/duck-soup
    ```

=== "From a clone"

    The data lives in [`duck_soup/tutorial/`](https://github.com/henrik716/duck-soup/tree/main/duck_soup/tutorial).
    Run `duck-soup tutorial` in a separate folder rather than the repository root, to keep the
    copies out of your working tree.

`duck-soup tutorial` never overwrites existing files, so a config you've been editing is safe.
Add `--force` to start over. To use another folder, pass it as an argument
(`duck-soup tutorial path/to/folder`); to open it in the editor, see
[Where the editor keeps your files](../getting-started/install.md#where-the-editor-keeps-your-files).

## The datasets

| Source id | File | Geometry | Columns |
|---|---|---|---|
| `districts` | `districts.geojson` | 4 polygons | `district_name`, `ward` (Upstream/Downstream), `population` |
| `places` | `places.geojson` | 11 points | `name`, `category` (a code like `MUS`), `opened` (a year, as text), `bread_crumbs` (handed out per year), `inspected` (dates in mixed formats) |
| `parks` | `parks.geojson` | 4 polygons | `name`, `kind` (park/meadow) |
| `river` | `river.geojson` | 1 polygon | `name` |
| `stops` | `stops.geojson` | 7 points | `stop_name`, `stop_type` (`B` bus, `T` tram, `P` paddle boat) |
| `new_places` | `new_places.geojson` | 2 points | `name`, `category` |
| `paths` | `paths.geojson` | 4 lines | `name` (the footpath network) |
| `lit_paths` | `lit_paths.geojson` | 3 lines | `lamps` (`LED`/`gas`): the lamp-lit stretches of the paths |
| `boardwalks` | `boardwalks.geojson` | 3 lines | `material`: the stretches of path that are boardwalk |
| `categories` | `categories.csv` | none (table) | `code`, `label` |

A few features are there on purpose, to show what happens at the edges:

- The **Old Lighthouse** stands alone outside town, so joins against districts find nothing
  for it.
- The **Old Duck House** has an empty category, and the Old Lighthouse has a code (`LND`) that
  isn't in `categories.csv`.
- **Breadcrumb Hill** has `opened` = `unknown`, and `inspected` mixes `2024-03-14`,
  `14.03.2024` and `March 2024`, which is useful when explaining `cast`.
- **`lit_paths`** and **`boardwalks`** lie on the footpaths, but were digitised separately: their
  lines are a few centimetres off, and they start and stop in the middle of a path.

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
  - {id: paths,      format: geojson, uri: data/tutorial/paths.geojson,      crs: EPSG:4326}
  - {id: lit_paths,  format: geojson, uri: data/tutorial/lit_paths.geojson,  crs: EPSG:4326}
  - {id: boardwalks, format: geojson, uri: data/tutorial/boardwalks.geojson, crs: EPSG:4326}
  - {id: categories, format: csv,     uri: data/tutorial/categories.csv}
```

## Run every example yourself

`pipelines/pondsworth.yaml` holds every example from these pages as its own pipeline, each
writing one layer named after the example. From the folder you ran `duck-soup tutorial` in:

```bash
duck-soup run pipelines/pondsworth.yaml
```

Then open `output/pondsworth_tutorial.gpkg` in QGIS and compare the layers. To explore them
with live previews instead, pick **pondsworth** in the editor's **load** list and use the
eye buttons on the steps.

!!! note "The pages are generated from real runs"
    The result tables and before/after diagrams on these pages come from running the examples
    through duck soup itself ([`scripts/build_tutorial.py`](https://github.com/henrik716/duck-soup/blob/main/scripts/build_tutorial.py)),
    so they show exactly what the engine produces.

## Where to go next

- [Steps by example](steps.md): every step type, with a diagram and its output.
- [Mapping by example](mapping.md): `from`, `const`, `expr`, `func`, `codelist` and `cast`.
