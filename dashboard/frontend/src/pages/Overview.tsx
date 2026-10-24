import { BoardOverview } from "../components/board/BoardOverview";
import { AppShell } from "../components/ui";
import { useSessions } from "../state/hooks";

export default function Overview() {
  const { sessions, conn } = useSessions();
  return <AppShell conn={conn} breadcrumb={<span>overview</span>}><BoardOverview sessions={sessions} connecting={conn === "connecting"}/></AppShell>;
}
