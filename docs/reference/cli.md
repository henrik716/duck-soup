# Command line

Installing `duck-soup-etl` gives you the `duck-soup` command. If it isn't on your `PATH`,
`python -m duck_soup.cli` works the same way.

## `duck-soup check`

```bash
duck-soup check pipelines/my_pipeline.yaml
```

Validates the file without reading any data. On success it prints:

```text
OK: 'my_pipeline' with 2 pipeline(s), output: 'output/result.gpkg'
```

On failure it prints the validation error, including the path to the bad field
(`pipelines.0.steps.2.source`), and exits non-zero. Good for CI.

## `duck-soup run`

```bash
duck-soup run quickstart.yaml
```

Runs every pipeline in the file and writes the output (GeoPackage or GeoParquet), logging each stage as it goes:

```text
  source 'places' (geojson) -> "src_places"
  source 'districts' (geojson) -> "src_districts"
  step 1: spatial_join intersects districts (on_multiple=first)
  writing output/quickstart.gpkg layer 'places' (EPSG:32631)

Wrote output/quickstart.gpkg
```

Relative paths in the YAML resolve against the directory you run the command from.

## `duck-soup tutorial`

```bash
duck-soup tutorial [folder] [--force]
```

Copies the [Pondsworth tutorial](../tutorials/index.md) into `folder` (default: the current
one): the sample data into `data/tutorial/`, and a config with every tutorial example into
`pipelines/pondsworth.yaml`. Then, from that folder, `duck-soup run pipelines/pondsworth.yaml`
runs them all.

It refuses to overwrite existing files unless you pass `--force`. It ends by telling you how to
open the config in the editor: directly if `folder` is the editor's project folder, otherwise by
setting `DUCK_SOUP_ROOT`.

## `duck-soup serve`

```bash
duck-soup serve [--host 127.0.0.1] [--port 8000]
```

Starts the web editor.

| Option | Default | Meaning |
|---|---|---|
| `--host` | `127.0.0.1` | Interface to bind. Use `0.0.0.0` to allow other machines (as the Docker image does). |
| `--port` | `8000` | Port. |

| Environment variable | Default | Meaning |
|---|---|---|
| `DUCK_SOUP_ROOT` | `~/duck-soup` (`/data` in Docker) | Project folder. Saved configs go in `<root>/pipelines/`, and the file browser starts here. |

!!! danger "Don't expose the editor to untrusted networks"
    The editor can read and browse files on the server and run arbitrary SQL expressions. It
    has no authentication. Keep it on `127.0.0.1`, or behind something that controls access.

## Exit codes

`check` and `run` exit with `0` on success and `1` on any failure. The error goes to stderr.
Schedulers and CI can rely on this.

## Scheduling runs

A run is one command, so cron, systemd timers, Windows Task Scheduler, Docker and CI can all
schedule it. [Scheduling runs](../scheduling.md) has a recipe for each, and shows how to keep
a failed run from deleting the last good output.
