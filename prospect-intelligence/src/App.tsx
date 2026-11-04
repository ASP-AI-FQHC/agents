import { useState } from "react";
import Dashboard from "./pages/Dashboard";
import Search from "./pages/Search";
import Prospects from "./pages/Prospects";
import Lists from "./pages/Lists";
import Pipeline from "./pages/Pipeline";
import Reports from "./pages/Reports";
import Settings from "./pages/Settings";
import About from "./pages/About";
import Profile from "./pages/Profile";

const NAV = ["Dashboard", "Find Prospects", "Prospect Intelligence", "My Lists", "Sales Pipeline", "Reports", "Settings", "About IT Architechs"] as const;
type Page = (typeof NAV)[number];

export default function App() {
  const [page, setPage] = useState<Page>("Dashboard");
  const [open, setOpen] = useState<number | null>(null);
  const [q, setQ] = useState("");
  const go = (p: Page) => { setPage(p); setOpen(null); };
  return (
    <div className="flex h-full">
      <aside className="flex w-56 shrink-0 flex-col bg-brand-900 text-white print:hidden">
        <div className="px-4 py-5"><div className="text-base font-semibold leading-tight">IT Architechs</div><div className="text-xs text-brand-100">Prospect Intelligence</div></div>
        <nav className="flex-1 space-y-1 px-2" aria-label="Main">
          {NAV.map((n) => <button key={n} onClick={() => go(n)} className={`w-full rounded-md px-3 py-2 text-left text-sm ${page === n ? "bg-brand-600" : "hover:bg-white/10"}`}>{n}</button>)}
        </nav>
        <div className="px-4 py-3 text-xs italic text-brand-100">Discover. Qualify. Connect.</div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-slate-200 bg-white px-6 py-2 print:hidden">
          <input aria-label="Search saved prospects" className="input w-96" placeholder="Search saved prospects by name, city or tag…" value={q} onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") go("Prospect Intelligence"); }} />
          <span className="text-xs text-slate-500">Searches this computer's saved records only</span>
        </header>
        <main className="min-w-0 flex-1 overflow-auto p-6">
          {open !== null ? <Profile id={open} onBack={() => setOpen(null)} /> :
            page === "Dashboard" ? <Dashboard go={(p) => go(p as Page)} /> :
            page === "Find Prospects" ? <Search onOpen={setOpen} /> :
            page === "Prospect Intelligence" ? <Prospects onOpen={setOpen} initialText={q} /> :
            page === "My Lists" ? <Lists onOpen={setOpen} /> :
            page === "Sales Pipeline" ? <Pipeline onOpen={setOpen} /> :
            page === "Reports" ? <Reports /> :
            page === "Settings" ? <Settings /> : <About />}
        </main>
      </div>
    </div>
  );
}
