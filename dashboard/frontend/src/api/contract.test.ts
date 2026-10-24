import { describe, expect, it } from "vitest";
import type { SessionDetail } from "./types";

// A real `SessionDetail.model_dump(mode="json")` of the `full_detail` fixture in
// backend/tests/test_models.py. The annotation is the check: `tsc --noEmit` fails on any key
// the backend serializes that types.ts lacks (excess property) or any key types.ts requires
// that the dump omits. Regenerate it from the fixture when models.py changes.
const pydanticDump: SessionDetail = {
  "id": "s",
  "project": "p",
  "run": "r",
  "path": "/p",
  "rev": 2,
  "status": "active",
  "counts": { "running": 1, "finished": 0, "exited": 0, "unknown": 0 },
  "last_activity_at": 2.0,
  "latest_milestone": "M1",
  "findings_total": 1,
  "running_workers": [],
  "lead": {
    "label": "Lead",
    "model": "claude",
    "confidence": "explicit-stream",
    "source_path": "docs/streams/x.md",
    "source_line": 4,
    "evidence": "owner: Lead",
    "conflicts": ["progress says otherwise"]
  },
  "stream": "redesign-v4",
  "cycle_progress": {
    "completed": 1,
    "current": 2,
    "phase": "build",
    "events": [
      {
        "phase": "plan",
        "cycle": 1,
        "label": "plan done",
        "at": 1.0,
        "source_path": "PROGRESS.md",
        "source_line": 1
      }
    ],
    "over_policy": true
  },
  "source_warnings": ["stale STATE.md"],
  "worker_previews": [
    {
      "name": "w1",
      "model": "gpt",
      "engine": "codex",
      "started_at": 1.0,
      "now": { "kind": "tool", "text": "grep", "tool": "grep" },
      "status": "running",
      "pid": 42,
      "title": "Build",
      "goal": "Ship it",
      "finished_at": null,
      "last_activity_at": 2.0,
      "summary": ["step"],
      "has_brief": true,
      "has_report": false,
      "log_stem": "w1",
      "log_size": 5,
      "log_format": "codex-text",
      "flags": ["stale"],
      "engine_family": "codex",
      "account": null,
      "effort": "medium"
    }
  ],
  "worker_total": 3,
  "activity_series": [{ "at": 1.0, "count": 4 }],
  "lat": {
    "section": "active", "lead_state": "live", "lead_kind": "codex", "level_tag": "LEAD", "project": "p",
    "parent_session": null, "root_session": "s", "launch_depth": 0, "session_id": "s", "session_name": "session",
    "lead_model": "gpt", "effort": "high", "counts": { "live": 1, "failed": 0, "noreport": 0, "done": 0, "total": 1 },
    "tokens_total": 100, "cost_total": 0.1, "goal": "Ship it"
  },
  "workers": [
    {
      "name": "w1",
      "status": "running",
      "pid": null,
      "model": "gpt",
      "engine": "codex",
      "title": null,
      "goal": null,
      "has_brief": false,
      "has_report": false,
      "started_at": 1.0,
      "finished_at": 3.0,
      "last_activity_at": 2.0,
      "now": { "kind": "text", "text": "working", "tool": null },
      "summary": [],
      "log_stem": "w1",
      "log_size": 5,
      "log_format": "text",
      "flags": [],
      "duration_s": 2.0,
      "log_mtime": 2.5,
      "artifact_count": 1,
      "engine_family": null,
      "account": null,
      "effort": null,
      "mode": null,
      "access": null,
      "access_source": null,
      "job": null,
      "worker_events": [],
      "log_series": [],
      "step_events": [],
      "tokens": null,
      "tokens_source": null
    }
  ],
  "milestones": ["M1"],
  "reviews": [],
  "extra_logs": [],
  "history": [
    {
      "phase": "review",
      "cycle": 2,
      "label": null,
      "at": 2.0,
      "source_path": null,
      "source_line": null
    }
  ],
  "artifacts": [
    {
      "name": "brief.md",
      "kind": "brief",
      "size": 10,
      "mtime": 1.0,
      "previewable": true,
      "preview_url": "/api/sessions/s/workers/w1/brief"
    }
  ],
  "log_index": [
    {
      "session_id": "s",
      "worker_name": "w1",
      "artifact_name": "brief.md",
      "kind": "brief",
      "mtime": 1.0,
      "size": 10,
      "preview_url": "/api/sessions/s/workers/w1/brief"
    }
  ],
  "pick_records": []
};

describe("backend contract parity", () => {
  it("reads a real Pydantic dump through the SessionDetail type", () => {
    expect(pydanticDump.lead?.confidence).toBe("explicit-stream");
    expect(pydanticDump.cycle_progress?.current).toBe(2);
    expect(pydanticDump.worker_previews[0].engine_family).toBe("codex");
    expect(pydanticDump.log_index?.[0].kind).toBe("brief");
    expect(pydanticDump.lat?.section).toBe("active");
  });
});
