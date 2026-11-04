import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { VERTICALS, type Geography, type Prospect, type Region } from "../lib/types";
import { Tier } from "../components/Bits";

type Mode = Geography["kind"];
const SEARCHABLE = VERTICALS.filter((v) => v !== "transportation" && v !== "other");

export default function Search({ onOpen }: { onOpen: (id: number) => void }) {
  const [mode, setMode] = useState<Mode>("county");
  const [state, setState] = useState("MO");
  const [counties, setCounties] = useState<Region[]>([]);
  const [county, setCounty] = useState("");
  const [city, setCity] = useState("");
  const [zip, setZip] = useState("");
  const [center, setCenter] = useState("");
  const [miles, setMiles] = useState(25);
  const [verts, setVerts] = useState<string[]>([...SEARCHABLE]);
  const [results, setResults] = useState<Prospect[]>([]);
  const [saved, setSaved] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [imp, setImp] = useState("");

  useEffect(() => { api.regions(state).then((r) => { const en = r.filter((x) => x.enabled); setCounties(en); setCounty(en[0]?.name ?? ""); }).catch((e) => setErr(String(e))); }, [state]);

  const geo = (): Geography => {
    switch (mode) {
      case "state": return { kind: "state", state };
      case "county": return { kind: "county", state, county };
      case "city": return { kind: "city", state, city };
      case "zip": return { kind: "zip", zip };
      case "radius": return { kind: "radius", query: center, miles };
    }
  };
  const run = async () => {
    setBusy(true); setErr(""); setResults([]);
    try { setResults(await api.search(geo(), verts)); } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };
  const keep = async (p: Prospect) => {
    const id = await api.save({ ...p, id: 0 });
    setSaved((s) => ({ ...s, [p.sourceId ?? p.name]: id }));
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between"><h1 className="text-xl font-semibold text-brand-900">Find Prospects</h1>
        <button className="btn btn-ghost" onClick={async () => { try { const r = await api.importCsv(); if (r) setImp(`Imported ${r.inserted} new, merged ${r.merged} duplicate(s), skipped ${r.skipped}.${r.errors.length ? " " + r.errors.slice(0, 3).join("; ") : ""}`); } catch (e) { setErr(String(e)); } }}>Import CSV lead list</button></div>
      {imp && <div className="rounded bg-emerald-50 p-2 text-sm text-emerald-800">{imp}</div>}
      <div className="card space-y-3 p-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm">Search by
            <select className="input mt-1 block" value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
              <option value="state">Entire state</option><option value="county">County</option><option value="city">City</option>
              <option value="zip">ZIP code</option><option value="radius">Radius around a location</option>
            </select></label>
          {mode !== "zip" && mode !== "radius" && (
            <label className="text-sm">State
              <select className="input mt-1 block" value={state} onChange={(e) => setState(e.target.value)}>
                <option value="MO">Missouri</option><option value="IL">Southern Illinois</option>
              </select></label>)}
          {mode === "county" && (
            <label className="text-sm">County
              <select className="input mt-1 block" value={county} onChange={(e) => setCounty(e.target.value)}>
                {counties.map((c) => <option key={c.id}>{c.name}</option>)}
              </select></label>)}
          {mode === "city" && <label className="text-sm">City<input className="input mt-1 block" value={city} onChange={(e) => setCity(e.target.value)} /></label>}
          {mode === "zip" && <label className="text-sm">ZIP<input className="input mt-1 block w-28" maxLength={5} value={zip} onChange={(e) => setZip(e.target.value)} /></label>}
          {mode === "radius" && (<>
            <label className="text-sm">Center (address, city or ZIP)<input className="input mt-1 block w-64" value={center} onChange={(e) => setCenter(e.target.value)} /></label>
            <label className="text-sm">Miles<input type="number" min={1} max={100} className="input mt-1 block w-24" value={miles} onChange={(e) => setMiles(+e.target.value)} /></label></>)}
        </div>
        <div className="flex flex-wrap gap-3 text-sm">
          {SEARCHABLE.map((v) => (
            <label key={v} className="flex items-center gap-1 capitalize">
              <input type="checkbox" checked={verts.includes(v)} onChange={(e) => setVerts(e.target.checked ? [...verts, v] : verts.filter((x) => x !== v))} />{v}</label>))}
        </div>
        <div className="flex items-center gap-3">
          <button className="btn btn-primary" disabled={busy || verts.length === 0} onClick={run}>{busy ? "Searching…" : "Search"}</button>
          <span className="text-xs text-slate-500">Source: OpenStreetMap (free, crowd-sourced; no employee counts or contacts). Add paid providers in Settings.</span>
        </div>
        {err && <div className="rounded bg-red-50 p-2 text-sm text-red-700">{err}</div>}
      </div>
      {results.length > 0 && (
        <div className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-100 text-left text-xs uppercase text-slate-500"><tr><th className="p-2">Company</th><th>Type</th><th>City</th><th>Fit</th><th /></tr></thead>
            <tbody>{results.map((p) => {
              const k = p.sourceId ?? p.name; const id = saved[k];
              return (<tr key={k} className="border-t border-slate-100">
                <td className="p-2 font-medium">{p.name}</td><td className="capitalize">{p.vertical}</td><td>{p.city ?? "—"}</td>
                <td><Tier tier={p.tier} /></td>
                <td className="pr-2 text-right">{id ? <button className="btn btn-ghost" onClick={() => onOpen(id)}>Open</button> : <button className="btn btn-primary" onClick={() => keep(p)}>Save</button>}</td>
              </tr>);})}</tbody>
          </table>
          <div className="p-2 text-xs text-slate-500">{results.length} results. Industry (NAICS), headcount and contacts are not available from this source; add them by import or manually. Fit tiers are computed on save.</div>
        </div>)}
    </div>
  );
}
