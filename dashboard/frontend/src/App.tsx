import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import Overview from "./pages/Overview";
import Run from "./pages/Run";
import Quota from "./pages/Quota";
import Logs from "./pages/Logs";
import Runs from "./pages/Runs";
import WorkerModal from "./components/WorkerModal";
import CommandPalette from "./components/CommandPalette";
import AlertsMenu from "./components/AlertsMenu";
import { routeTable } from "./components/shell/routeTable";

function LegacySessionRedirect() {
  const { sessionId = "", worker } = useParams();
  const { search } = useLocation();
  const sid = encodeURIComponent(sessionId);
  const name = worker ? `/workers/${encodeURIComponent(worker)}` : "";
  return <Navigate replace to={{ pathname: `/runs/${sid}${name}`, search }}/>;
}

/** The run Files page is gone; old `/runs/:sid/files` links land on the run summary. */
function LegacyFilesRedirect() {
  const { sid = "" } = useParams();
  const { search } = useLocation();
  return <Navigate replace to={{ pathname: `/runs/${encodeURIComponent(sid)}`, search }}/>;
}

function RunWithWorkerModal() {
  return <><Run/><WorkerModal/></>;
}

export default function App() {
  return (
    <>
      <Routes>
        <Route path={routeTable.overview} element={<Overview/>}/>
        <Route path={routeTable.quota} element={<Quota/>}/>
        <Route path={routeTable.logs} element={<Logs/>}/>
        <Route path="/runs" element={<Runs/>}/>
        <Route path={routeTable.run} element={<Run/>}/>
        <Route path={routeTable.timeline} element={<Run/>}/>
        <Route path={routeTable.workers} element={<Run/>}/>
        <Route path={routeTable.reviews} element={<Run/>}/>
        <Route path="/runs/:sid/files" element={<LegacyFilesRedirect/>}/>
        <Route path={routeTable.worker} element={<RunWithWorkerModal/>}/>
        <Route path="/s/:sessionId" element={<LegacySessionRedirect/>}/>
        <Route path="/s/:sessionId/w/:worker" element={<LegacySessionRedirect/>}/>
        <Route path="*" element={<Navigate replace to="/"/>}/>
      </Routes>
      <CommandPalette/>
      <AlertsMenu/>
    </>
  );
}
