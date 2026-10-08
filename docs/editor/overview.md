# Tour of the editor

The editor is a visual front end for the [pipeline YAML](../reference/yaml.md). Everything you
build in it becomes a YAML file you can save, commit and run from the
[command line](../reference/cli.md). Everything you can write in YAML can also be loaded into the
editor.

Start it with `duck-soup serve` (or the Docker image) and open <http://localhost:8000>.

![The editor: builder on the left, map and preview tabs on the right](../assets/screenshots/editor-overview-light.png#only-light){ .screenshot loading=lazy }
![The editor: builder on the left, map and preview tabs on the right](../assets/screenshots/editor-overview-dark.png#only-dark){ .screenshot loading=lazy }

The left side is where you **build**. The right side is where you **see** the result.

## Top bar

| Control | What it does |
|---|---|
| **load** | Opens a config saved in the project folder's `pipelines/` directory. The editor reopens the last config you used on startup. |
| **new** | Starts a blank config with one empty pipeline. |
| **import** | Opens a dialog where you paste a pipeline YAML from anywhere (git, email, a colleague). It replaces what's open. |
| **save as** | The file name used by **save** (`pipelines/<name>.yaml`). It defaults to the config name. |
| **validate** | Re-checks the config now. The editor already validates automatically shortly after every change. |
| **save** | Writes the YAML. The button is highlighted when you have unsaved changes, and the editor warns before you navigate away and lose them. |
| **run** | Runs every pipeline and writes the output file. The log appears in **Run Logs**. |
| **☀ / ☾** | Switches the editor between dark and light mode. It follows your OS setting until you click it, and then remembers your choice. The map's basemap switches with it. |
| **status** | Shows `valid`, `validating…`, `running…` or the first line of an error. Click it to open the **Problems** tab. |

## Lineage diagram

The **ETL Pipeline Lineage** panel at the top draws the config as a flow chart: sources feed
into the base, steps run left to right, and layers converge on the shared output. It
redraws as you edit. Click any node to jump to its card, and drag to pan when the diagram is
wider than the screen. Click the header to collapse it.

![Lineage diagram for a multi-pipeline config](../assets/screenshots/lineage-light.png#only-light){ .screenshot loading=lazy }
![Lineage diagram for a multi-pipeline config](../assets/screenshots/lineage-dark.png#only-dark){ .screenshot loading=lazy }

## Config card and metadata

- **config name**: the config's `name`.
- **output file**: the `output` path every pipeline writes into. A `.gpkg` path writes a
  GeoPackage, a `.parquet` path writes GeoParquet (one file per layer when there's more than
  one). The folder icon opens the file browser.
- **Dataset Metadata**: optional catalogue fields (name, abstract, origin, update frequency,
  geometric/attribute quality, access method, GDPR note). They're saved in the config's
  `metadata:` block so they travel with the pipeline definition.

## Pipeline cards

Each pipeline is a card with a name field, a remove button, and five section tabs. Each tab
shows a short summary of what it contains:

| Tab | Covers | Guide |
|---|---|---|
| **sources** | the datasets, the base, the working CRS | [Sources](sources.md) |
| **derived sources** | filtered/buffered views of sources | [Sources](sources.md#derived-sources) |
| **steps** | joins and geoprocessing, in order | [Steps](steps.md) |
| **mapping** | the output columns | [Mapping](mapping.md) |
| **output layers** | layer names, CRS, per-layer filters and mappings | [Output layers](output-layers.md) |

**+ add pipeline** at the bottom adds another independent pipeline that writes into the same
output.

## Right-hand panel

- **Map**: a MapLibre map of the current preview, with a Light/Dark basemap switch. Hover a
  feature to highlight it, and click it to see its attributes.
- **Data Table**: the same preview rows as a sortable, filterable table.
- **YAML Config**: the generated YAML with line numbers, plus **copy** and **export .py**.
- **Run Logs**: output from every run, so you can compare runs.
- **Problems**: validation errors, each with a link that jumps to the card causing it.

See [Preview, validate & run](preview-and-run.md) for details.

## Resizing the panels

![A wider preview panel with the map collapsed, so the table gets the full height](../assets/screenshots/resized-panels-light.png#only-light){ .screenshot loading=lazy }
![A wider preview panel with the map collapsed, so the table gets the full height](../assets/screenshots/resized-panels-dark.png#only-dark){ .screenshot loading=lazy }

- Drag the line between the builder and the right-hand panel to make either side wider.
- Drag the line between the map and the tabs to give the map or the table more height.
- The small **⌃** tab in the middle of that line collapses the map to a thin strip.
  The toolbar stays visible, so **view**, **limit** and **in view** still work, and the table
  gets the full height. Click the button again, or drag the line down, to bring the map back.
- Double-click a dividing line to reset it. You can also focus a line with <kbd>Tab</kbd> and
  move it with the arrow keys.

The editor remembers the sizes in your browser. On narrow screens the panels stack vertically
and the dividers are hidden.

## Keyboard shortcuts

| Keys | Action |
|---|---|
| <kbd>Ctrl</kbd>+<kbd>S</kbd> | save |
| <kbd>Ctrl</kbd>+<kbd>Enter</kbd> | run |
| <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>V</kbd> | validate now |
| <kbd>Ctrl</kbd>+<kbd>Z</kbd> / <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd> | undo / redo a structural change |
| <kbd>Alt</kbd>+<kbd>↑</kbd> / <kbd>Alt</kbd>+<kbd>↓</kbd> | move the focused step or mapping row |
| <kbd>←</kbd> / <kbd>→</kbd> | switch tabs when a tab button has focus |

Use <kbd>Cmd</kbd> instead of <kbd>Ctrl</kbd> on macOS.

**Undo/redo** covers structural changes: adding, removing or reordering cards, auto-map,
loading and starting new configs. Text you type in a field uses the browser's own undo inside
that field.
