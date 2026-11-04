import { useEffect, useMemo, useState } from "react";
import { api, type Filter } from "../lib/api";
import { STAGES, type Category, type Prospect } from "../lib/types";
import { Empty, NA, Origin, Tier } from "../components/Bits";

type SortKey = "name" | "city" | "vertical" | "employeesEst" | "score" | "stage" | "updatedAt";

export default function Prospects({ onOpen, initialText }: { onOpen: (id: number) => void; initialText: string }) {
  const [rows, setRows] = useState<Prospect[]>([]);
  const [f, setF] = useState<Filter>({ text: initialText || undefined });
  const [cats, setCats] = useState<Category[]>([]);
  const [lists, setLists] = useState<[number, string, number][]>([]);
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [sort, setSort] = useState<{ k: SortKey; asc: boolean }>({ k: "score", asc: false });
  const [searches, setSearches] = useState<[number, string, string][]>([]);
  const [msg, setMsg] = useState(""); const [loading, setLoading] = useState(true);
  const load = () => { setLoading(true); api.prospects(f).then(setRows).finally(() => setLoading(false)); };
  useEffect(load, [JSON.stringify(f)]);
  useEffect(() => { api.naics().then(setCats); api.lists().then(setLists); api.savedSearches("prospects").then(setSearches); }, []);
  useEffect(() => { if (initialText) setF((x) => ({ ...x, text: initialText })); }, [initialText]);

  const sorted = useMemo(() => [...rows].sort((a, b) => {
    const x = a[sort.k] ?? "", y = b[sort.k] ?? ""; const r = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
    return sort.asc ? r : -r; }), [rows, sort]);
  const th = (k: SortKey, label: string) => <th className="cursor-pointer select-none p-2" onClick={() => setSort({ k, asc: sort.k === k ? !sort.asc : true })}>{label}{sort.k === k ? (sort.asc ? " ▲" : " ▼") : ""}</th>;
  const ids = sel.size ? [...sel] : sorted.map((r) => r.id);
  const set = <K extends keyof Filter>(k: K, v: Filter[K] | "") => setF((x) => ({ ...x, [k]: v === "" ? undefined : v }));

  const doExport = async () => { const skipped = await api.exportCsv(ids); if (skipped !== null) setMsg(`Exported ${ids.length - skipped} record(s)${skipped ? `; ${skipped} withheld by source license` : ""}.`); };
  const addToList = async (v: string) => {
    if (!v) return; let id = +v;
    if (v === "new") { const n = prompt("New list name"); if (!n) return; id = await api.createList(n); }
    await api.addToList(id, [...sel]); setMsg(`Added ${sel.size} to list.`); api.lists().then(setLists);
  };
  const saveSearch = async () => { const n = prompt("Name this search"); if (n) { await api.saveSearch(n, "prospects", f); api.savedSearches("prospects").then(setSearches); } };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between"><h1 className="text-xl font-semibold text-brand-900">Prospect Intelligence</h1>
        <div className="flex gap-2"><button className="btn btn-ghost" onClick={saveSearch}>Save search</button><button className="btn btn-primary" onClick={doExport} disabled={!rows.length}>Export {sel.size ? "selected" : "filtered"} (CSV)</button></div></div>
      <div className="card flex flex-wrap items-end gap-2 p-3 text-sm">
        <input className="input w-56" placeholder="Search name, city, tag" value={f.text ?? ""} onChange={(e) => set("text", e.target.value)} />
        <select className="input" value={f.naics?.[0] ?? ""} onChange={(e) => set("naics", e.target.value ? [e.target.value] : undefined)}><option value="">All industries (NAICS)</option>
          {cats.map((c) => <option key={c.id} value={c.id}>{c.group}: {c.label}</option>)}</select>
        <select className="input" value={f.state ?? ""} onChange={(e) => set("state", e.target.value)}><option value="">All states</option><option>MO</option><option>IL</option></select>
        <select className="input" value={f.tier ?? ""} onChange={(e) => set("tier", e.target.value)}><option value="">All tiers</option><option>A</option><option>B</option><option>C</option><option>Low</option></select>
        <select className="input" value={f.stage ?? ""} onChange={(e) => set("stage", e.target.value)}><option value="">All stages</option>{STAGES.map((s) => <option key={s}>{s}</option>)}</select>
        <select className="input" value={f.origin ?? ""} onChange={(e) => set("origin", e.target.value)}><option value="">All origins</option><option value="live">Live</option><option value="imported">Imported</option><option value="manual">Manual</option></select>
        <input className="input w-24" type="number" placeholder="Min emp." onChange={(e) => set("minEmployees", e.target.value ? +e.target.value : "")} />
        <input className="input w-24" type="number" placeholder="Max emp." onChange={(e) => set("maxEmployees", e.target.value ? +e.target.value : "")} />
        {searches.length > 0 && <select className="input" value="" onChange={(e) => { const s = searches.find((x) => x[0] === +e.target.value); if (s) setF(JSON.parse(s[2])); }}><option value="">Saved searches…</option>{searches.map((s) => <option key={s[0]} value={s[0]}>{s[1]}</option>)}</select>}
        <button className="btn btn-ghost" onClick={() => setF({})}>Clear</button>
      </div>
      {sel.size > 0 && <div className="flex items-center gap-2 text-sm"><b>{sel.size} selected</b>
        <select className="input" value="" onChange={(e) => addToList(e.target.value)}><option value="">Add to list…</option><option value="new">+ New list…</option>{lists.map((l) => <option key={l[0]} value={l[0]}>{l[1]}</option>)}</select>
        <button className="btn btn-ghost" onClick={() => setSel(new Set())}>Clear selection</button></div>}
      {msg && <div className="rounded bg-emerald-50 p-2 text-sm text-emerald-800">{msg}</div>}
      <div className="card overflow-auto"><table className="w-full text-sm">
        <thead className="bg-slate-100 text-left text-xs uppercase text-slate-500"><tr><th className="p-2"><input aria-label="Select all" type="checkbox" checked={sel.size > 0 && sel.size === sorted.length} onChange={(e) => setSel(e.target.checked ? new Set(sorted.map((r) => r.id)) : new Set())} /></th>
          {th("name", "Company")}{th("city", "Location")}{th("vertical", "Industry")}{th("employeesEst", "Employees")}<th>Revenue</th><th>Contact</th>{th("score", "Score")}{th("stage", "Status")}{th("updatedAt", "Updated")}</tr></thead>
        <tbody>{sorted.map((p) => (
          <tr key={p.id} className="border-t border-slate-100 hover:bg-brand-50">
            <td className="p-2"><input aria-label={`Select ${p.name}`} type="checkbox" checked={sel.has(p.id)} onChange={(e) => { const s = new Set(sel); e.target.checked ? s.add(p.id) : s.delete(p.id); setSel(s); }} /></td>
            <td className="cursor-pointer p-2 font-medium" onClick={() => onOpen(p.id)}>{p.name} <Origin o={p.origin} /></td>
            <td>{[p.city, p.state].filter(Boolean).join(", ") || "—"}</td><td className="capitalize">{p.naics ? `${p.vertical} · ${p.naics}` : <>{p.vertical} <span className="text-xs text-amber-600">(NAICS not verified)</span></>}</td>
            <td><NA v={p.employeesEst} /></td><td><NA v={p.revenueRange} /></td><td><NA v={p.phone} /></td>
            <td><Tier tier={p.tier} /> <span className="text-xs text-slate-500">{p.score}</span></td><td className="capitalize">{p.stage.replace(/_/g, " ")}</td><td className="text-xs text-slate-500">{p.updatedAt?.slice(0, 10)}</td></tr>))}</tbody></table>
        {loading ? <Empty>Loading…</Empty> : rows.length === 0 && <Empty>No saved prospects match. Use Find Prospects or import a CSV lead list.</Empty>}</div>
      <p className="text-xs text-slate-500">{rows.length} record(s). Data completeness is shown on each profile and is separate from the fit score.</p>
    </div>
  );
}
