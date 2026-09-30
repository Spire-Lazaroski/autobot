import { useEffect, useState, type ComponentType } from "react";
import { useHealth } from "./lib/api";
import { Lab } from "./pages/Lab";
import { Live } from "./pages/Live";
import { Search } from "./pages/Search";
import { Shifting } from "./pages/Shifting";
import { WalkForward } from "./pages/WalkForward";

interface Page {
  id: string;
  label: string;
  Component: ComponentType;
}

export const PAGES: Page[] = [
  { id: "lab", label: "Backtest Lab", Component: Lab },
  { id: "search", label: "Param search", Component: Search },
  { id: "walkforward", label: "Walk-forward", Component: WalkForward },
  { id: "shifting", label: "Strategy shifting", Component: Shifting },
  { id: "live", label: "Live monitor", Component: Live },
];

function useHashPage() {
  const read = () => window.location.hash.replace(/^#\/?/, "") || PAGES[0].id;
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
  const Current = (PAGES.find((p) => p.id === page) ?? PAGES[0]).Component;
  return (
    <div className="min-h-full flex flex-col">
      <header className="sticky top-0 z-40 bg-bg/95 backdrop-blur border-b border-line">
        <div className="flex items-center gap-1 px-3 h-11 overflow-x-auto">
          <span className="num font-bold text-[13px] mr-3 whitespace-nowrap">
            <span className="text-up">▲</span> autobot<span className="text-muted font-normal"> lab</span>
          </span>
          {PAGES.map((p) => (
            <a key={p.id} href={`#/${p.id}`}
              className={`px-2.5 py-1 rounded text-[12px] font-semibold whitespace-nowrap ${p.id === page ? "bg-panel-2 text-fg" : "text-muted hover:text-fg"}`}>
              {p.label}
            </a>
          ))}
          <span className="ml-auto flex items-center gap-1.5 text-[11px] text-muted whitespace-nowrap pl-3">
            <span className={`h-2 w-2 rounded-full ${health.isError ? "bg-down" : health.data ? "bg-up" : "bg-dim"}`} />
            {health.isError ? "API down" : "API"}
            <span className="chip bg-warn/15 text-warn ml-1">paper only</span>
          </span>
        </div>
      </header>
      <main className="flex-1 p-2 sm:p-3 max-w-[1800px] w-full mx-auto">
        <Current />
      </main>
    </div>
  );
}
