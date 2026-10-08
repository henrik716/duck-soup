# Scheduling runs

A pipeline is a YAML file and a run is one command, so any scheduler that can run a shell
command can run duck soup: cron, systemd timers, Windows Task Scheduler, a container
platform or a CI pipeline. duck soup has no built-in scheduler and needs none.

This page covers the checks to make before you schedule anything, a recipe for each common
scheduler, and how to keep a failed run from leaving users with a broken or missing file.

## Before you schedule

Most scheduled jobs that fail work fine from a terminal. They fail because the scheduler starts
them in a different folder, with a different `PATH` and as a different user. Check these
first:

1. **Run it by hand first**, from the folder the job will run in:

    ```bash
    duck-soup check pipelines/nightly.yaml
    duck-soup run pipelines/nightly.yaml
    ```

2. **Fix the working directory.** Relative `uri`s and output paths resolve against the folder
   the command is *run from*, not against the YAML file's folder. Every recipe below either
   `cd`s into the project folder or sets the task's start folder. You can also use absolute
   paths in the YAML.

3. **Use the full path to `duck-soup`.** Schedulers start with a minimal `PATH` and won't
   find it otherwise. To find the path:

    === "Linux / macOS"

        ```bash
        command -v duck-soup      # pipx: usually ~/.local/bin/duck-soup
        ```

    === "Windows"

        ```powershell
        (Get-Command duck-soup).Source
        ```

4. **Rely on the exit code.** `duck-soup run` and `duck-soup check` exit with `0` on success
   and non-zero on any failure: an invalid config, an unreachable service, a SQL error. The
   error and traceback go to stderr, so capture both streams in your log.

5. **Keep `overwrite: true`** (the default) for jobs that rebuild a dataset. With
   `overwrite: false`, every run appends to the existing output.

## Recipes

### cron (Linux, macOS)

A small wrapper script keeps the crontab line short and gives you one place for logging,
locking and alerts. Save it next to your pipelines, for example as `run-nightly.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /srv/gis                                   # project folder: relative paths resolve here
exec /home/gis/.local/bin/duck-soup run pipelines/nightly.yaml
```

Make it executable (`chmod +x run-nightly.sh`), then add a line with `crontab -e`:

```bash
# min hour day month weekday   — every night at 02:00
0 2 * * * flock -n /tmp/duck-soup-nightly.lock /srv/gis/run-nightly.sh >> /srv/gis/logs/nightly.log 2>&1
```

- `flock -n` skips this run if the previous one is still going, so slow runs never overlap.
  (`flock` is part of util-linux. On macOS, drop it or install it with Homebrew.)
- `>> … 2>&1` appends both the progress log and any error to one file.
- `%` has a special meaning in a crontab line. If you put a `date +%F` there, escape it as
  `\%`, or move it into the wrapper script.

### systemd timer (Linux)

A timer has two advantages over cron. It logs to the journal, and with `Persistent=true` it
catches up on a run missed while the machine was off. Create two files in
`/etc/systemd/system/`:

```ini title="duck-soup-nightly.service"
[Unit]
Description=duck soup: nightly rebuild

[Service]
Type=oneshot
User=gis
WorkingDirectory=/srv/gis
ExecStart=/home/gis/.local/bin/duck-soup run pipelines/nightly.yaml
```

```ini title="duck-soup-nightly.timer"
[Unit]
Description=Run duck soup nightly

[Timer]
OnCalendar=*-*-* 02:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable the timer, then check it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now duck-soup-nightly.timer
systemctl list-timers duck-soup-nightly.timer      # next run
journalctl -u duck-soup-nightly.service            # output of past runs
sudo systemctl start duck-soup-nightly.service     # run once now, to test
```

A `oneshot` service doesn't start a second copy while one is running, so runs can't overlap.

### Windows Task Scheduler

In PowerShell, register a task that runs every night at 02:00:

```powershell
$action = New-ScheduledTaskAction `
  -Execute "C:\Users\gis\.local\bin\duck-soup.exe" `
  -Argument "run pipelines\nightly.yaml" `
  -WorkingDirectory "D:\gis"
$trigger = New-ScheduledTaskTrigger -Daily -At 2am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "duck soup nightly" -Action $action -Trigger $trigger -Settings $settings
```

To do the same in the Task Scheduler app: **Create Task…**, add a trigger, then add a
**Start a program** action with the full path to `duck-soup.exe`, the arguments
`run pipelines\nightly.yaml`, and the project folder in **Start in**.

- **Start in** (`-WorkingDirectory`) is what makes relative paths work. A task without it
  runs in `C:\Windows\System32`.
- `-StartWhenAvailable` runs a missed task once the machine is back on, and
  `MultipleInstances IgnoreNew` stops runs from overlapping.
- To run while nobody is logged in, open the task's **General** tab and choose **Run whether
  user is logged on or not**. The account needs access to the data folders and to any
  network shares.
- Task Scheduler doesn't keep the program's output. To get a log, run a small `.ps1` script
  instead:

    ```powershell title="run-nightly.ps1"
    Set-Location D:\gis
    & "C:\Users\gis\.local\bin\duck-soup.exe" run pipelines\nightly.yaml *>> logs\nightly.log
    exit $LASTEXITCODE
    ```

    Then use `powershell.exe` as the program, with the arguments
    `-NoProfile -ExecutionPolicy Bypass -File D:\gis\run-nightly.ps1`.

### Docker

The image's working directory is `/data`, so mount your project folder there and override the
command:

```bash
docker run --rm -v /srv/gis:/data ghcr.io/henrik716/duck-soup \
  duck-soup run pipelines/nightly.yaml
```

Put that line in cron, a systemd service or Task Scheduler in place of the `duck-soup`
command. A container platform such as Kubernetes (`CronJob`), Nomad (`periodic`) or a cloud
scheduler can run the same image and command, as long as the data is on a volume the job can
reach.

`--rm` removes the container after each run. Images are only updated when you pull them, so a
scheduled job keeps running the version you last pulled.

### GitHub Actions (or any CI)

When the sources are online services (WFS, OGC API - Features, ArcGIS REST, PostgreSQL), a CI
runner can do the work, and you need no server of your own:

```yaml title=".github/workflows/nightly.yml"
name: nightly
on:
  schedule:
    - cron: "0 2 * * *"      # UTC
  workflow_dispatch: {}       # adds a "Run workflow" button for testing

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install duck-soup-etl
      - run: duck-soup run pipelines/nightly.yaml
      - uses: actions/upload-artifact@v4
        with:
          name: nightly-output
          path: output/
```

- GitHub runs scheduled workflows in UTC, and may start them late when its runners are busy.
- Add a final step to publish the output where it's needed (object storage, a file share,
  a release asset). The upload-artifact step above only keeps the output in the run's
  artifacts.
- Put database passwords in a repository secret and pass them as `PGPASSWORD`
  (`env: {PGPASSWORD: ${{ secrets.PGPASSWORD }}}` on the run step), not in the YAML. See
  [Credentials](#credentials).

### Other orchestrators

Airflow, Dagster, Prefect, Jenkins, Azure DevOps and similar tools can all run a shell
command. Run `duck-soup run <file>` from the project folder, and treat a non-zero exit code as
a failure.

For a Python-based orchestrator, the editor's **export .py** button also helps. It writes one
`.py` file with the pipeline YAML embedded in it. The file needs `duck-soup-etl` installed, has
no other files to keep in sync, and exits non-zero on failure like the CLI.

## Safe runs

### Don't leave users without a file when a run fails

With `overwrite: true`, the old output is **deleted before the sources are read**. If a source
is unreachable at 02:00, the run fails and leaves no file, or a partial one. Users opening the
file mid-run can also see half-written data.

To avoid both, write to a staging path, and replace the published file only after a successful
run:

```yaml
output: output/staging/roads.gpkg     # the pipeline writes here…
```

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /srv/gis
/home/gis/.local/bin/duck-soup run pipelines/nightly.yaml
mv output/staging/roads.gpkg /srv/share/roads.gpkg   # …and only a good run reaches users
```

Because of `set -e`, a failed run stops the script before the `mv`, so yesterday's file stays
in place. On the same filesystem, `mv` replaces the file in a single step, so readers see
either the old file or the new one, never half of each. On Windows, use
`Move-Item -Force` after checking `$LASTEXITCODE`.

To keep history as well, copy the file with a date in its name after the move:
`cp /srv/share/roads.gpkg /srv/archive/roads-$(date +%F).gpkg`.

### Get told when it fails

A scheduled job that fails without telling anyone can go unnoticed for weeks. Some options:

- **cron** emails a job's output when `MAILTO=you@example.org` is set at the top of the
  crontab and the machine can send mail. Leave out the `>> log` redirect if you want the mail.
- **systemd**: add `OnFailure=` to the service and point it at a unit that sends a
  notification.
- **Task Scheduler** shows the **Last Run Result** (`0x0` means success). Monitoring tools can
  watch it, or the wrapper script can send an alert itself.
- **Any wrapper script** can call a webhook (Slack, Teams, an uptime monitor) when the run
  fails:

    ```bash
    /home/gis/.local/bin/duck-soup run pipelines/nightly.yaml \
      || curl -fsS -X POST -d "duck soup nightly failed on $(hostname)" https://hooks.example.org/alert
    ```

- **GitHub Actions** emails the person who last edited the workflow's `cron` line when a
  scheduled run fails.

### Credentials

The YAML has no variable substitution. Anything in a `uri` is stored in plain text, so:

- **PostgreSQL**: leave the password out of the connection string
  (`postgresql://etl@db.internal:5432/gis`) and provide it as the `PGPASSWORD` environment
  variable in the scheduled job. In CI, set it from a secret.
- Make the pipeline folder readable only by the account that runs the job.
- Don't commit a YAML file that contains a password.

### Run history

Every `duck-soup run` records itself in `runs/<config name>/` in the project folder: when it
ran, whether it worked, the error if it didn't, and how many rows each layer got. The editor
shows these in its [History tab](editor/preview-and-run.md#run-history), next to the runs
started from the editor, so you can check last night's run without opening a log file.

For scheduled runs to show up there, they have to record into the editor's project folder.
The recipes above `cd` into it (or set it as the working directory), which is enough. If the
job runs from somewhere else, set `DUCK_SOUP_ROOT` to the project folder, or pass
`--history-dir /srv/gis`. The run's own log, as captured by your scheduler, is unaffected.

The newest 200 runs per config are kept. `--no-history` turns recording off for a job.

### Validate in CI

`duck-soup check` reads no data, so it's cheap to run on every commit. It catches a broken
config before the nightly run does:

```yaml
- run: pip install duck-soup-etl
- run: for f in pipelines/*.yaml; do duck-soup check "$f"; done
```
