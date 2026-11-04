import { useState } from "react";
import Search from "./pages/Search";
import Prospects from "./pages/Prospects";
import Settings from "./pages/Settings";
import Profile from "./pages/Profile";

type Page = "search" | "prospects" | "settings";
const NAV: { id: Page; label: string }[] = [
  { id: "search", label: "Discover" }, { id: "prospects", label: "My Prospects" }, { id: "settings", label: "Settings" },
];

export default function App() {
  const [page, setPage] = useState<Page>("search");
  const [open, setOpen] = useState<number | null>(null);
  return (
    <div className="flex h-full">
      <aside className="flex w-56 flex-col bg-brand-900 text-white">
        <div className="px-4 py-5">
          <div className="text-base font-semibold leading-tight">IT Architechs</div>
          <div className="text-xs text-brand-100">Prospect Intelligence</div>
        </div>
        <nav className="flex-1 space-y-1 px-2">
          {NAV.map((n) => (
            <button key={n.id} onClick={() => { setPage(n.id); setOpen(null); }}
              className={`w-full rounded-md px-3 py-2 text-left text-sm ${page === n.id ? "bg-brand-600" : "hover:bg-white/10"}`}>
              {n.label}
            </button>
          ))}
        </nav>
        <div className="px-4 py-3 text-xs italic text-brand-100">Discover. Qualify. Connect.</div>
      </aside>
      <main className="min-w-0 flex-1 overflow-auto p-6">
        {open !== null ? <Profile id={open} onBack={() => setOpen(null)} /> :
          page === "search" ? <Search onOpen={setOpen} /> :
          page === "prospects" ? <Prospects onOpen={setOpen} /> : <Settings />}
      </main>
    </div>
  );
}
