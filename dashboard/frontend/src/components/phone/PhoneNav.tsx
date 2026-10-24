import { Link, useLocation } from "react-router-dom";
import { isMockMode } from "../../api/client";
import { useSessions } from "../../state/hooks";
import { isPhoneNavActive, phoneNavItems } from "./phoneNav";

/** Fixed bottom navigation for phone widths. CSS in styles/phone.css hides it above 600px. */
export function PhoneNav() {
  const { pathname, search } = useLocation();
  const { sessions } = useSessions();
  const items = phoneNavItems(sessions, isMockMode(), search);
  return (
    <nav className="phone-nav" aria-label="Primary navigation, phone">
      {items.map((item) => {
        // Plain Link with aria-current, not NavLink: NavLink cannot match Runs against any /runs/:sid
        // target, and it drops an explicit aria-current when its own match is false.
        const active = isPhoneNavActive(item, pathname);
        if (item.to === null) {
          return <span key={item.key} className="phone-nav__item" aria-disabled="true" title="No runs">{item.label}</span>;
        }
        return (
          <Link key={item.key} to={item.to} className="phone-nav__item" aria-current={active ? "page" : undefined}>
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
