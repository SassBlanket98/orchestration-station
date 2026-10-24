import { Link, useLocation } from "react-router-dom";
import { isMockMode } from "../api/client";
import { useSessions } from "../state/hooks";
import { withSearch } from "./shell/navigation";
import { alertsMockFixture } from "../mocks/alerts";
import { type AlertItem, type AlertKind, deriveAlerts, formatAlertTime } from "./alerts/derive";
import "./alerts/alerts.css";

const ICON_PATHS: Record<AlertKind, { tone: "bad" | "warn"; path: string }> = {
  stdin_hang: { tone: "bad", path: "M12 8v5m0 3.5v.5M4.5 19.5h15L12 5.5 4.5 19.5Z" },
  idle: { tone: "warn", path: "M9 6v12m6-12v12" },
  review: { tone: "bad", path: "M12 5.5 19.5 19h-15L12 5.5Z" },
  exited: { tone: "bad", path: "m6 6 12 12M18 6 6 18" },
};

function AlertIcon({ kind }: { kind: AlertKind }) {
  const { tone, path } = ICON_PATHS[kind];
  return (
    <svg className={`alerts-icon--${tone}`} viewBox="0 0 24 24" aria-hidden="true">
      <path d={path} />
    </svg>
  );
}

export default function AlertsMenu({ open = false, onClose }: { open?: boolean; onClose?: () => void }) {
  const location = useLocation();
  const { sessions } = useSessions();
  if (!open) return null;
  const items: AlertItem[] = isMockMode() ? alertsMockFixture() : deriveAlerts(sessions, Date.now() / 1000);
  return (
    <div className="shell-popover alerts-menu" role="dialog" aria-label="Alerts">
      <div className="alerts-head">
        <h3>Attention <span className="alerts-count">{items.length}</span></h3>
        <span className="alerts-order">latest first</span>
      </div>
      {items.length === 0 ? (
        <p className="alerts-empty">No alerts</p>
      ) : (
        <ul className="alerts-list">
          {items.map((item) => (
            <li key={item.id}>
              <Link className="alerts-row" to={withSearch(item.href, location.search)} onClick={onClose}>
                <AlertIcon kind={item.kind} />
                <span className="alerts-text">{item.text}</span>
                <span className="alerts-time">{formatAlertTime(item.at)}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
