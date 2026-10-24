# Station Dashboard

The dashboard is a cache and UI over the station monitor. Start the station monitor first; the default
upstream is `http://127.0.0.1:8743`. The dashboard API listens on `127.0.0.1:8744` by default.
The station uses a 120 s window when checking whether a PID may have been reused.

## Run

Build the frontend and start the dashboard from this directory:

```sh
scripts/serve.sh
```

For frontend development, use `scripts/dev.sh` and open `http://127.0.0.1:5191`.

## Configuration

The config file is `~/.config/stagewatch/config.toml`; set `STAGEWATCH_CONFIG` to use another path.
The available keys are:

| Key | Default | Meaning |
|---|---|---|
| `port` | `8744` | Dashboard API port on loopback. |
| `scan_interval_s` | `1.5` | Seconds between session scans. |
| `discover_interval_s` | `15` | Seconds between searches for new sessions. |
| `auth` | unset | Optional sign-in configuration. |
| `quota_service_url` | unset | Loopback URL of the quota service. |
| `quota_service_secret_file` | unset | File containing the quota service bearer. |
| `monitor_url` | `http://127.0.0.1:8743` | Loopback station monitor URL; set to `null` to disable. |
| `push_vapid_private_key_file` | unset | Optional VAPID private key file. |
| `push_subject` | `mailto:admin@localhost` | VAPID contact subject. |
| `push_store_file` | `~/.config/stagewatch/push.json` | Browser push subscription store. |

## Access

The sign-in gate is designed for use behind a Cloudflare tunnel. Its public-request check trusts the
`Cf-Connecting-Ip` header: a request without that header is treated as local and skips sign-in.
There is no setting to change this, so any other reverse proxy in front of the dashboard bypasses the
gate. Keep the dashboard on loopback unless it is behind the intended Cloudflare tunnel.
