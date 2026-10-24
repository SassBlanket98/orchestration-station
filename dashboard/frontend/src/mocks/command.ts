import type { CommandItem } from "../components/command/model";

/** Illustrative palette rows matching the approved menus mockup. */
export const commandMockItems: CommandItem[] = [
  { id: "run-billing-api-ram", group: "Jump to", label: "billing-api / ram-2026-10-08", detail: "Run · 4 live", status: "active", searchText: "run billing-api ram-2026-10-08", to: "/runs/run:L2hvbWUvdXNlci9Qcm9qZWN0cy9iaWxsaW5nLWFwaS8uc3RhZ2UvcmFtLTIwMjYtMTAtMDg" },
  { id: "run-stagewatch", group: "Jump to", label: "stagewatch / redesign", detail: "Run · 2 live", status: "active", searchText: "run stagewatch redesign", to: "/runs/stagewatch" },
  { id: "worker-billing-api-fix1", group: "Workers", label: "fix1 · gpt-6-luna", detail: "running", status: "running", searchText: "run billing-api ram-2026-10-08 fix1 gpt-6-luna codex", to: "/runs/run:L2hvbWUvdXNlci9Qcm9qZWN0cy9iaWxsaW5nLWFwaS8uc3RhZ2UvcmFtLTIwMjYtMTAtMDg/workers/ram-codex", engine: "codex" },
  { id: "worker-stagewatch-audit", group: "Workers", label: "audit-expanded-ui", detail: "idle 4 m", status: "idle", searchText: "run stagewatch redesign audit-expanded-ui claude", to: "/runs/run:L2hvbWUvdXNlci9Qcm9qZWN0cy9iaWxsaW5nLWFwaS8uc3RhZ2UvcmFtLTIwMjYtMTAtMDg/workers/ram-cursor", engine: "cursor" },
];
