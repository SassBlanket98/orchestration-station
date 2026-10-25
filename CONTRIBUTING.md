# Contributing

Thanks for helping. Bug reports, fixes and small features are welcome.

## Before you start

- For anything bigger than a small fix, open an issue first and describe the change. This saves you work if the idea does not fit.
- The station uses the Python standard library only. Do not add third-party packages to `station/`.
- The monitor is read-only by default. Changes that write to stage files, or start or stop processes, must stay behind `--allow-actions`.

## Check before you push

`scripts/check.sh` runs the same checks as CI. Install it as a pre-push hook once:

```sh
scripts/install-hooks.sh
```

Pull requests run CI before review. Pull requests from first-time contributors wait for a maintainer to approve the run.

## Station (`station/`)

The station is a set of modules in `station/src/`. `build.py` joins them into one file, `station/station`. Edit `station/src/`, never the built file.

```sh
cd station
python3 build.py        # writes station/station
tests/run.sh            # unit tests
uvx ruff@0.17.2 check src tests build.py
```

Add or update tests in `station/tests/` for any behaviour you change. New lint findings are not accepted.

Try your change on the sample stage directory:

```sh
./station --stage ../examples/.stage --plain
./station --demo
```

## Station Dashboard (`dashboard/`)

Frontend:

```sh
cd dashboard/frontend
pnpm install
pnpm typecheck
pnpm test
pnpm build
```

Backend:

```sh
cd dashboard/backend
uv run pytest
```

## Stage files

The files the station reads are described in [docs/STAGE-CONTRACT.md](docs/STAGE-CONTRACT.md). If your change reads a new file or field, update that document in the same pull request.

## Pull requests

- Keep each pull request to one change.
- Say what you tested, and on which system (Linux distribution or macOS version, terminal).
- Do not commit secrets, `.env` files, logs or large binaries.

By contributing, you agree that your contribution is licensed under the MIT license in [LICENSE](LICENSE).
