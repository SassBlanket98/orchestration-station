# Security

## Reporting a problem

Please do not open a public issue for a security problem.

Use GitHub's private vulnerability reporting instead: open the repository's **Security** tab and choose **Report a vulnerability**. Include the version or commit, the steps to reproduce, and what an attacker could do.

You should get a first answer within a week. Fixes are released as soon as they are ready, and the report is credited unless you ask otherwise.

## Supported versions

Only the latest commit on the default branch is supported.

## Design notes

These are the security defaults. A report that shows one of them can be bypassed is in scope.

- The web monitor binds to `127.0.0.1` unless you pass `--host`.
- The monitor is read-only. Actions that start or stop workers are off unless you pass `--allow-actions`.
- The station reads local files and runs only the commands you configure (for example `STATUS_QUOTA_CMD`). It sends nothing to outside services.

Out of scope: problems that need you to expose the monitor with `--host` and `--allow-actions` to people you do not trust, or that need someone who can already write to your stage directories or run commands as your user.
