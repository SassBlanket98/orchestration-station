# Orchestration Station

Orchestration Station shows what your local AI orchestrators and their workers are doing. It reads plain files from `.stage/` directories. It has three parts:

- `station`, a command-line tool. It prints a snapshot, or serves a web monitor.
- The web monitor, served by `station --serve` at http://127.0.0.1:8743.
- The Station Dashboard, a web UI in `dashboard/`. Its API uses port 8744. Its development server uses port 5191.

The monitor reads files. It does not change stage files. Actions that start or stop workers are off unless you pass `--allow-actions`.

## Requirements

- Linux. The station reads process data from `/proc`. It does not run on other systems.
- Python 3.12 or newer.
- uv. Used for the Python checks of the repository.
- pnpm. Used to build and check the Station Dashboard.

## Quickstart

This uses the sample stage directory in `examples/.stage/`. No real project is needed.

1. Build the station. From the `station/` directory, run:

   ```sh
   python3 build.py
   ```

   This writes `station/station`.

2. Install it for your user. This copies the file to `~/.local/bin/station`:

   ```sh
   sh install.sh
   ```

   Make sure `~/.local/bin` is on your `PATH`.

3. Print one snapshot of the example. Run this from the repository root:

   ```sh
   station --stage examples/.stage --plain
   ```

4. Serve the monitor on the example:

   ```sh
   station --stage examples/.stage --serve --port 8743
   ```

   Open http://127.0.0.1:8743 in a browser.

The example has two workers:

- `alpha` has a pid file for a process that does not run. The station shows it as `noreport`, not `running`. A pid file counts only while its process runs.
- `beta` has a `REPORT.md`. The station shows it as `done`.

See [docs/STAGE-CONTRACT.md](docs/STAGE-CONTRACT.md) for the files the station reads.

## Command-line flags

| flag | meaning |
|---|---|
| `--serve` | serve the read-only web monitor |
| `--port N` | web monitor port. The default is 8743. |
| `--host ADDR` | bind address. Default `127.0.0.1`. Change it only if you need access from another machine, and know who can reach it. |
| `--stage DIR` | read one stage directory |
| `--global` | show every orchestrator found under the roots |
| `--roots DIR [DIR ...]` | roots to search in global mode. Without this flag, the default roots from the source are used. |
| `--plain` | print one plain-text snapshot and exit |
| `--json` | print one JSON snapshot and exit |
| `--execution ID` | with `--json`, print the full detail of one execution |
| `--compact` or `--full` | overview density |
| `--everything` | plain output without a row limit |
| `--all` | start with all history shown |
| `--fps N` | frame rate while animating, 1 to 30 |
| `--no-notify` | no desktop notifications |
| `--demo` | show generated demo data |

Global mode example, for a monitor on all your projects:

```sh
station --global --serve --port 8743
```

## Read-only monitor and actions

The monitor is read-only by design. It shows state. It does not start or stop workers.

Actions that start or stop workers are off by default. Pass `--allow-actions` to turn them on:

```sh
station --global --serve --port 8743 --allow-actions
```

Use `--allow-actions` only on a monitor that only you can reach.

## Optional integrations

These are off by default:

- Quota CLI integration. The station calls a quota command when one is set up.
- OpenClaw peers. Set through `STATION_OPENCLAW_*` environment variables.

Setup for these is not covered in this guide yet.

## User service

`deploy/station-monitor.service` runs the global monitor as a user service on port 8743. `install.sh --with-unit` copies it to `~/.config/systemd/user/`. Neither step enables or starts the service. To run it, do this yourself:

```sh
systemctl --user daemon-reload
systemctl --user enable --now station-monitor.service
```

## Layout

- `station/`: the station source and `build.py`. The build writes `station/station`.
- `dashboard/`: the Station Dashboard.
- `examples/.stage/`: a sample stage directory for the quickstart.
- `docs/STAGE-CONTRACT.md`: the files the station reads from `.stage/`.
- `deploy/station-monitor.service`: user service unit.
- `install.sh`: copies the built station to `~/.local/bin/station`.

## Station Dashboard

The Station Dashboard lives in `dashboard/`. It is built with pnpm:

```sh
cd dashboard
pnpm install
pnpm build
```

API port 8744. Development server port 5191.

## Tools

- `station/tools/worker-session` records which lead session launched a worker and carries lineage to nested workers.
- `station/tools/status-integrations.py` prepares, applies, and restores harness hook integrations without printing configuration contents.
- `station/station-register` records station execution metadata for a stage directory.

The station build writes `station/station` and `station/station-register`; `install.sh` installs `station/station`.

## License

MIT. See [LICENSE](LICENSE). Copyright 2026 Orchestration Station contributors.
