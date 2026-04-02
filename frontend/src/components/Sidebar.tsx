import { Activity, Bot, BriefcaseBusiness, LayoutDashboard, LogOut, Moon, Settings, Sun } from "lucide-react";
import type { ViewId } from "../lib/types";

const NAV_ITEMS: Array<{ id: ViewId; label: string; icon: typeof LayoutDashboard }> = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "bots", label: "Bots", icon: Bot },
  { id: "analytics", label: "Analytics", icon: Activity },
  { id: "accounts", label: "Journal", icon: BriefcaseBusiness },
  { id: "settings", label: "Settings", icon: Settings },
];

interface SidebarProps {
  activeView: ViewId;
  operatorName: string;
  overallState: string;
  themeMode: "dark" | "light";
  onNavigate: (view: ViewId) => void;
  onToggleTheme: () => void;
  onLogout: () => void;
}

export function Sidebar({ activeView, operatorName, overallState, themeMode, onNavigate, onToggleTheme, onLogout }: SidebarProps) {
  const ThemeIcon = themeMode === "dark" ? Sun : Moon;
  const nextThemeLabel = themeMode === "dark" ? "Switch to light mode" : "Switch to dark mode";

  return (
    <aside className="app-sidebar">
      <div className="brand-stack">
        <div className="brand-mark">OB3</div>
        <div className="brand-copy-block">
          <p className="sidebar-eyebrow">Operator Deck</p>
          <h1>OmniBot</h1>
          <p className="sidebar-copy">FastAPI-backed runtime surface.</p>
        </div>
      </div>

      <nav className="sidebar-nav" aria-label="App navigation">
        {NAV_ITEMS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            className={`sidebar-link ${activeView === id ? "is-active" : ""}`}
            onClick={() => onNavigate(id)}
          >
            <span className="sidebar-link-icon"><Icon size={16} /></span>
            <span>{label}</span>
          </button>
        ))}
      </nav>

      <div className="sidebar-footer">
        <div className="sidebar-pulse">
          <span>Live surface</span>
          <strong>{overallState}</strong>
          <small>Signed in as {operatorName}</small>
        </div>
        <div className="sidebar-footer-actions">
          <button
            type="button"
            className="sidebar-link sidebar-link-icon-only"
            onClick={onToggleTheme}
            aria-label={nextThemeLabel}
            title={nextThemeLabel}
          >
            <span className="sidebar-link-icon"><ThemeIcon size={16} /></span>
          </button>
          <button type="button" className="sidebar-link sidebar-link-logout" onClick={onLogout}>
            <span className="sidebar-link-icon"><LogOut size={16} /></span>
            <span>Logout</span>
          </button>
        </div>
      </div>
    </aside>
  );
}