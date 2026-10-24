# Stage directory contract

This page lists the files the station reads from a stage directory. A lead or worker that writes these files gets a live view in the station. Each rule names the function that reads it. The source is in `src/collect.py` unless noted.

## Where the station looks

- A directory named `.stage` is a stage directory. Its label is the name of the folder that holds it. (`find_stages`; `STAGE_NAMES` in `src/model.py`)
- Global mode (`--global`) searches the roots for `.stage` directories up to 4 levels deep. (`find_stages`; `SCAN_DEPTH` in `src/model.py`)
- `--stage DIR` reads one directory as a stage directory. The directory itself holds the files below. The example in `examples/.stage/` works this way.

## Layout

```
.stage/
  <name>.pid          worker process id
  <name>.log          worker log (used when <name>.log is missing: <name>.txt)
  <name>.model        model name on the first line
  <name>.session      optional JSON, key lead_session
  <name>/REPORT.md    worker report
  briefs/<name>.md    worker brief (or the first briefs/<name>-*.md)
  PROGRESS.md         lead milestones, optional
  lead.json           lead status, optional
  review-<id>.log     review log
  review-<id>.pid     optional, for a running review
  review-<id>.model   optional, model for a review
```

`<name>` is the worker name. Use the same name for every file that belongs to one worker.

## Pid file

- The file holds the process id as digits. (`_co_stage_pid_names`)
- A worker is live when that process exists and its start time is within 120 seconds of the pid file's modification time. Write the pid file right after you start the process. (`_co_pid_live`, `_co_pid_reuse_window`)
- A pid file that names a live process that is not an agent is treated as a service, not a worker. (`src/collect.py`, near line 930)

A pid file for a process that is not running is not live. The worker then shows `noreport`, `failed`, or `done`. It never shows `running`. Old pid files can stay. They do not change a finished worker.

## Log

- The station reads `<name>.log`. If it is missing, it reads `<name>.txt`. (`_co_pick_log`)
- The station parses log lines by harness format. Step and error detection depend on that format. A log with no recognised steps shows no error. A worker that is not live, with no report and no error, shows `noreport`.
- A live worker with an empty log shows `running`. Some harnesses print nothing until they finish. (`_co_state`)
- A reasoning effort line in the last 16 KB of the log sets the effort. The line has the form `reasoning effort: <value>`. (`_co_one_worker`)

## Model file

- `<name>.model` holds the model name on its first line. (`_co_one_worker`)
- If the file has an effort word, the station shows it. The words are `low`, `medium` (or `med`), `high`, `xhigh`, and `max`. (`_co_one_worker`, `_co_effort_re`)

## Report

- `<name>/REPORT.md` is the worker report. A worker that is not live and has a report shows `done`. A live worker with a report keeps its live state (`running`, `quiet`, or `stalled`).
- The station shows the lines under `## Summary`. (`_co_report_summary`)

## Brief

Brief path: `briefs/<name>.md`. If that file is missing, the station uses the first `briefs/<name>-*.md`. (`_co_brief_path`)

- Title: the first line that starts with `# `. (`_co_brief_title`)
- Goal: the line that starts with `Goal:`. (`_co_brief_goal`)
- Job: the line that starts with `Job:`. The match ignores case. The station shows the first word in lower case. (`_co_brief_job`)
- Stdout only: a line that starts with `Output: stdout` (case-insensitive). A worker with this brief and no `REPORT.md` shows `done`. (`_co_brief_stdout_only`, `_co_stdout_output_re`)

## Session file

`<name>.session` is optional JSON. The station reads the key `lead_session` to find the lead that launched the worker. (`_co_worker_session`) Other keys exist. This page does not describe them.

## PROGRESS.md

- A line that starts with `HH:MM` is a timed milestone. Other lines stay as text. (`_co_milestones`)
- A milestone text of the form `dispatch: launched <name> <name>...` lists the workers launched together. (`_co_dispatch_re`)

## lead.json

Optional. The station reads these keys. (`_co_lead_file`, `_co_lead_json_pid`)

| key | value |
|---|---|
| `updated_at` | ISO 8601 time of the last update |
| `lead_label` or `lead_model` | name shown for the lead |
| `phase` | current phase, as text |
| `stream` | stream name; the brief file name without `.md` |
| `pid` | process id of the lead; a positive integer |

## Reviews

- A file named `review-<id>.log` is a review. The station shows it as done when the log has steps and no live pid is attached. (`_co_reviews`, `_co_state`)
- `review-<id>.pid` and `review-<id>.model` are optional. They work the same as the worker files.

## States

The station gives each worker one state. Rules come from `_co_state`, and the time limits come from `src/model.py`.

| state | rule |
|---|---|
| `running` | live, and the log is empty or the worker has been quiet for less than 90 s |
| `quiet` | live, and quiet for 90 s or more |
| `stalled` | live, quiet for 300 s or more, and CPU below 1.0 |
| `done` | not live, and either `REPORT.md` exists, or a review log has steps, or there is no `REPORT.md` and the brief says `Output: stdout` |
| `failed` | not live, no `REPORT.md`, and the last log step is an error |
| `noreport` | not live, no `REPORT.md`, and no error |

The quiet time is the time since the log last changed. The exact measure is in `_co_quiet`, which this page does not cover.

## See also

- `examples/.stage/` in this repository. It holds two example workers.
- `README.md` for the commands that read a stage directory.
