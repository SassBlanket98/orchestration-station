# Orchestration Station

Orchestration Station shows what your local AI orchestrators and their workers are doing. It reads plain files from `.stage/` directories. It has three parts:

- `station`, a terminal dashboard. Run it with no flags for the interactive view, or ask it for a plain-text or JSON snapshot.
- A read-only JSON API, served by `station --serve` at http://127.0.0.1:8743/api/state. It has no web page of its own.
- The Station Dashboard, the web UI in `dashboard/`. It reads the station's API. Its own API uses port 8744. Its development server uses port 5191.

The monitor reads files. It does not change stage files. Actions that start or stop workers are off unless you pass `--allow-actions`.

## Requirements

- Linux or macOS. On Linux the station reads process data from `/proc`; on macOS it uses `ps`, `lsof`
  and `sysctl`. macOS 15 hides other processes' environment, so leads started with a custom
  `CLAUDE_CONFIG_DIR` or `CODEX_HOME` are shown under the default account there. Windows: use WSL.
- Python 3.12 or newer. macOS ships 3.9; install a newer one with Homebrew (`brew install python`)
  or uv (`uv python install 3.12`).
- uv. Used for the Python checks of the repository.
- pnpm. Used to build and check the Station Dashboard.

## Quickstart

This uses the sample stage directory in `examples/.stage/`. No real project is needed.

1. Build the station. From the `station/` directory, run:

   ```sh
   python3 build.py
   ```

   This writes `station/station`.

2. Install it for your user. From the repository root, run this. It copies the file to `~/.local/bin/station`:

   ```sh
   sh install.sh
   ```

   Make sure `~/.local/bin` is on your `PATH`.

3. Print one snapshot of the example. Run this from the repository root:

   ```sh
   station --stage examples/.stage --plain
   ```

4. Open the interactive view of the example (press `q` to quit, `?` for keys):

   ```sh
   station --stage examples/.stage
   ```

5. Serve the JSON API on the example, and read it:

   ```sh
   station --stage examples/.stage --serve --port 8743
   curl http://127.0.0.1:8743/api/state
   ```

   For a browser view, run the Station Dashboard (see below).

The example has two workers:

- `alpha` has a pid file for a process that does not run. The station shows it as `noreport`, not `running`. A pid file counts only while its process runs.
- `beta` has a `REPORT.md`. The station shows it as `done`.

See [docs/STAGE-CONTRACT.md](docs/STAGE-CONTRACT.md) for the files the station reads.

## Command-line flags

| flag | meaning |
|---|---|
| `--serve` | serve the read-only JSON API |
| `--port N` | API port. The default is 8743. |
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
| `--tutorial` | interactive tour on demo data; touches nothing |
| `--version` | print the version |

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

- Usage data. The station runs commands you provide and shows their JSON on the usage screen. See below.
- OpenClaw sessions. Set `STATION_OPENCLAW_AGENT` to your OpenClaw agent id to show its sessions, and `STATION_OPENCLAW_PEERS` to a comma-separated list of chat peers to treat as your own.
- Spawn picker (experimental). With `--allow-actions`, the `n` key can start a worker through your own launcher. It reads `$STATION_HOME/picker/guards.json` and `models.json` and runs `$STATION_HOME/bin/dispatch` or `bin/launch-session`. These files are not part of this repository and their format is not documented yet; without them the picker lists nothing.

### Usage data

The usage screen (`u`) and the header show subscription quotas and API spend. The station gets them by running commands that print JSON. It ships no such command and holds no keys. Without one, the screen says so and the rest of the station works as usual.

| variable | default command | shows |
|---|---|---|
| `STATUS_QUOTA_CMD` | `$STATION_HOME/bin/quota status --json` | subscription windows and API credits |
| `STATUS_OPENROUTER_CMD` | `$STATION_HOME/bin/quota openrouter --json` | OpenRouter spend |
| `STATUS_OPENAI_CMD` | `$STATION_HOME/bin/quota openai --json` | OpenAI spend |

`STATION_HOME` defaults to `~/.local/state/station`. Each command runs once a minute, must exit 0 and print one JSON object. Each source is independent: spend still shows when the quota command is missing.

Quota command output (times are Unix milliseconds; every field except `subscriptions` is optional):

```json
{
  "subscriptions": [
    {"id": "my-plan", "limits": [
      {"name": "5h", "window": "five_hour", "usedPct": 42, "pacePct": 50, "resetsAt": 1793000000000}
    ]}
  ],
  "credits": [{"account": "api-credits", "balanceUsd": 12.5, "burnPerDayUsd": 1.2, "low": false}]
}
```

Spend command output (all fields optional, at least one needed):

```json
{
  "credit": {"balanceUsd": 14.2, "burnPerDayUsd": 1.8},
  "spend": {
    "monthToDateUsd": 21.4,
    "perDay": [{"date": "2026-10-24", "usd": 1.46, "requests": 59}],
    "perModel": [{"model": "vendor/model-a", "usd": 0.98, "requests": 40}]
  }
}
```

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

The Station Dashboard lives in `dashboard/`. Setup, configuration and remote access are described in [dashboard/README.md](dashboard/README.md). To build the frontend:

```sh
cd dashboard/frontend
pnpm install
pnpm build
```

`dashboard/scripts/dev.sh` runs it for development and `dashboard/scripts/serve.sh` serves a build.

API port 8744. Development server port 5191.

## Tools

- `station/tools/worker-session` records which lead session launched a worker and carries lineage to nested workers.
- `station/tools/status-integrations.py` prepares, applies, and restores harness hook integrations without printing configuration contents.
- `station/station-register` records station execution metadata for a stage directory.

The station build writes `station/station` and `station/station-register`; `install.sh` installs `station/station`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Report security problems privately, as described in [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE). Copyright 2026 Orchestration Station contributors.
