import { toLogin, useMe } from "../lib/api";
import { InstallApp } from "./InstallApp";
/**
 * The Spire Analytics header, reproduced from partials/spire_header.html
 * (its CSS lives in brand.css, copied rule for rule). On screens <= 760px the
 * page title hides (as in the source) and the nav moves to a bottom tab bar.
 */
export interface NavItem {
  id: string;
  label: string;
  icon: string; // Bootstrap Icons class
  admin?: boolean; // shown to the admin only
  user?: boolean;  // shown to invited users (their own Live, Reports, the shared News and Help, their Account)
  userOnly?: boolean;
}

export const NAV: NavItem[] = [
  { id: "live", label: "Live", icon: "bi-broadcast", user: true },
  { id: "lab", label: "Lab", icon: "bi-bar-chart-line" },
  { id: "search", label: "Search", icon: "bi-search" },
  { id: "shifting", label: "Shifting", icon: "bi-shuffle" },
  { id: "news", label: "News", icon: "bi-newspaper", user: true },
  { id: "recal", label: "Recalibrate", icon: "bi-sliders" },
  { id: "reports", label: "Reports", icon: "bi-file-earmark-text", user: true },
  { id: "help", label: "Help", icon: "bi-question-circle", user: true },
  { id: "account", label: "Account", icon: "bi-person-circle", user: true, userOnly: true },
  { id: "users", label: "Users", icon: "bi-people", admin: true },
];

export function SpireHeader({ active, title, status }: { active: string; title?: string; status?: React.ReactNode }) {
  const me = useMe();
  const role = me.data?.role;
  const nav = role === "user" ? NAV.filter((n) => n.user)                          // invited users: their own pages only
    : NAV.filter((n) => !n.userOnly && (!n.admin || role === "admin"));
  return (
    <>
      <header className="spire-hdr">
        <a href="#/live" style={{ display: "flex", alignItems: "center", gap: 10, textDecoration: "none" }} aria-label="Spire Analytics home">
          <span className="mark" aria-hidden="true" />
          <span><span className="word">SPIRE</span><span className="sub">ANALYTICS</span></span>
        </a>
        {title && <span className="page">{title}</span>}
        {status}
        <nav className="spire-hdr-nav">
          {nav.map((n) => (
            <a key={n.id} href={`#/${n.id}`} className={n.id === active ? "on" : undefined}>
              <i className={`bi ${n.icon}`} /> {n.label}
            </a>
          ))}
          <InstallApp />
          <SignOut />
        </nav>
      </header>
      <nav className="spire-tabs" aria-label="Pages">
        {nav.map((n) => (
          <a key={n.id} href={`#/${n.id}`} className={n.id === active ? "on" : undefined}>
            <i className={`bi ${n.icon}`} />{n.label}
          </a>
        ))}
      </nav>
    </>
  );
}

/** Shown only when the lab asks for a login (on the server): who is signed in, and Sign out. */
function SignOut() {
  const me = useMe();
  if (!me.data?.login_required || !me.data.user) return null;
  return (
    <a href="#" title={`Signed in as ${me.data.user}`} onClick={async (e) => {
      e.preventDefault();
      await fetch("/api/logout", { method: "POST" }).catch(() => undefined);
      toLogin();
    }}><i className="bi bi-box-arrow-right" /> Sign out</a>
  );
}
