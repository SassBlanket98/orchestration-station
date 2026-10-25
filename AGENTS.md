# orchestration-station — agent rules

Public repo (SassBlanket98/orchestration-station). How to build and test: [CONTRIBUTING.md](CONTRIBUTING.md).

## Before every push
- Run `scripts/check.sh` (same checks as CI; `scripts/check.sh station` etc. for one part). It must pass. Install the pre-push hook once per clone: `scripts/install-hooks.sh`.
- A check added to `.github/workflows/ci.yml` goes into `scripts/check.sh` in the same change.
- Never push with `--no-verify` unless David says so for that push.
- After pushing, check the branch's CI run (`gh run list --branch <branch> -L1`, `gh run view <id> --log-failed`) and fix failures before calling work done. macOS failures count too, even while that job is report-only.

## Lint and style
- No new lint findings. Station: `uvx ruff@0.17.2` (pinned; do not bump in passing). Backend: ruff from `uv.lock`. Frontend: `tsc --noEmit`.
- `station/` is standard library only. Edit `station/src/`, then `python3 build.py`; commit the rebuilt `station/station`.

## Repo
- `main` is protected by a ruleset: PR, green required checks, up-to-date branch, code-owner review, linear history. Work on a branch.
- Workflow, `scripts/`, `install.sh`, `deploy/` and `build.py` changes are CODEOWNERS paths: say why in the PR.

## Ports
8743 station API, 8744 dashboard API, 5191 dashboard web; scratch 4890-4899. Strict ports only.
