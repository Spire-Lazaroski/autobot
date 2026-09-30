import { useEffect, useState, type ComponentType } from "react";
import { SpireHeader } from "./components/SpireHeader";
import { useHealth } from "./lib/api";
import { usePageState } from "./lib/usePageState";
import { Help } from "./pages/Help";
import { Lab } from "./pages/Lab";
import { Live } from "./pages/Live";
import { Reports } from "./pages/Reports";
import { Search } from "./pages/Search";
import { Shifting } from "./pages/Shifting";
import { WalkForward } from "./pages/WalkForward";

function SearchHub() {
  const [tab, setTab] = usePageState<"search" | "walkforward">("search.tab", "search");
  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-1.5" role="tablist">
        {([["search", "Parameter search", "bi-grid-3x3"], ["walkforward", "Walk-forward", "bi-skip-forward"]] as const).map(([id, label, icon]) => (
          <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
            className={`btn ${tab === id ? "tab-on" : ""}`}><i className={`bi ${icon}`} />{label}</button>
        ))}
      </div>
      {tab === "search" ? <Search /> : <WalkForward />}
    </div>
  );
}

const PAGES: Record<string, { title: string; Component: ComponentType }> = {
  live: { title: "Live monitor", Component: Live },
  lab: { title: "Backtest lab", Component: Lab },
  search: { title: "Parameter search & walk-forward", Component: SearchHub },
  shifting: { title: "Strategy shifting", Component: Shifting },
  reports: { title: "Trading reports", Component: Reports },
  help: { title: "Help & operating procedure", Component: Help },
};

function useHashPage() {
  const read = () => {
    const h = window.location.hash.replace(/^#\/?/, "");
    if (h === "walkforward") return "search"; // old link
    return PAGES[h] ? h : "live";
  };
  const [page, setPage] = useState(read);
  useEffect(() => {
    const on = () => setPage(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return page;
}

export default function App() {
  const page = useHashPage();
  const health = useHealth();
  const { title, Component } = PAGES[page];
  useEffect(() => {
    document.body.classList.add("has-tabs");
    document.title = `${title} · Spire Analytics`;
  }, [title]);
  return (
    <div className="min-h-full flex flex-col">
      <div className="sticky top-0 z-40">
        <SpireHeader active={page} title={title} status={
          <span className="flex items-center gap-2 text-[11.5px] text-muted whitespace-nowrap">
            <span className="flex items-center gap-1.5" title={health.isError ? "The lab API is not answering" : "Lab API connected"}>
              <span className={`h-2 w-2 rounded-full ${health.isError ? "bg-down" : health.data ? "bg-up" : "bg-dim"}`} />
              {health.isError ? "API down" : "API"}
            </span>
            <span className="chip chip-warn"><i className="bi bi-shield-check mr-1" />paper only</span>
          </span>
        } />
      </div>
      <main className="flex-1 p-3 sm:p-4 max-w-[1800px] w-full mx-auto">
        <Component />
      </main>
    </div>
  );
}
