import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Icp, Region } from "../lib/types";

const PROVIDERS: { id: string; label: string; status: string }[] = [
  { id: "google_places", label: "Google Places API key", status: "Credential storage ready; search adapter not yet implemented" },
  { id: "apollo", label: "Apollo API key", status: "Credential storage ready; adapter not yet implemented" },
  { id: "hunter", label: "Hunter.io API key", status: "Credential storage ready; adapter not yet implemented" },
  { id: "zoominfo", label: "ZoomInfo credential", status: "Credential storage ready; adapter not yet implemented" },
  { id: "gateway_token", label: "Shared gateway token", status: "Optional authenticated backend gateway; not yet implemented" },
];

export default function Settings() {
  const [secrets, setSecrets] = useState<Record<string, boolean>>({});
  const [vals, setVals] = useState<Record<string, string>>({});
  const [regions, setRegions] = useState<Region[]>([]);
  const [newCounty, setNewCounty] = useState(""); const [err, setErr] = useState(""); const [info, setInfo] = useState("");
  const [icp, setIcp] = useState<Icp | null>(null); const [conn, setConn] = useState<string>("Not tested");
  useEffect(() => { api.icp().then(setIcp); }, []);
  const load = () => {
    api.secretStatus().then((s) => setSecrets(Object.fromEntries(s))).catch((e) => setErr(String(e)));
    api.regions("IL").then(setRegions);
  };
  useEffect(load, []);
  const wrap = (f: () => Promise<unknown>) => async () => { setErr(""); try { await f(); load(); } catch (e) { setErr(String(e)); } };

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-brand-900">Settings</h1>
      {err && <div className="rounded bg-red-50 p-2 text-sm text-red-700">{err}</div>}
      {info && <div className="rounded bg-emerald-50 p-2 text-sm text-emerald-800">{info}</div>}
      <section className="card space-y-2 p-4"><h2 className="font-semibold">Integrations</h2>
        <div className="flex flex-wrap items-center gap-3 text-sm"><span className="w-56">OpenStreetMap (Nominatim + Overpass)</span><span className={conn.startsWith("Connected") ? "text-emerald-700" : "text-slate-500"}>{conn}</span>
          <button className="btn btn-ghost" onClick={async () => { setConn("Testing…"); try { setConn(await api.testConnection("osm")); } catch (e) { setConn(`Failed: ${e}`); } }}>Test connection</button></div></section>
      {icp && <section className="card space-y-3 p-4"><h2 className="font-semibold">Ideal Customer Profile & scoring</h2>
        <div className="grid grid-cols-2 gap-2 text-sm md:grid-cols-4">{(Object.keys(icp.weights) as (keyof Icp["weights"])[]).map((k) => <label key={k} className="capitalize">{k} weight<input type="number" min={0} className="input mt-0.5 block w-full" value={icp.weights[k]} onChange={(e) => setIcp({ ...icp, weights: { ...icp.weights, [k]: +e.target.value } })} /></label>)}</div>
        <div className="text-xs text-slate-500">Weights total {Object.values(icp.weights).reduce((a, b) => a + b, 0)} (must be 100)</div>
        <div className="grid grid-cols-2 gap-2 text-sm md:grid-cols-5">
          {([["tier_a", "Priority A ≥"], ["tier_b", "Priority B ≥"], ["tier_c", "Priority C ≥"], ["min_employees", "Min employees"], ["max_employees", "Max employees"]] as const).map(([k, l]) => <label key={k}>{l}<input type="number" className="input mt-0.5 block w-full" value={icp[k]} onChange={(e) => setIcp({ ...icp, [k]: +e.target.value })} /></label>)}</div>
        <button className="btn btn-primary" onClick={async () => { setErr(""); try { await api.saveIcp(icp); setInfo("ICP saved; all prospects re-scored."); } catch (e) { setErr(String(e)); } }}>Save & re-score</button></section>}
      <section className="card space-y-2 p-4"><h2 className="font-semibold">Your data</h2>
        <p className="text-sm text-slate-600">Records are stored only on this computer in a local database. Installed copies do not share data.</p>
        <div className="flex flex-wrap gap-2"><button className="btn btn-ghost" onClick={wrap(async () => { if (await api.backup()) setInfo("Backup saved."); })}>Back up database…</button>
          <button className="btn btn-ghost" onClick={wrap(async () => { if (confirm("Restoring replaces ALL current data with the backup. Continue?") && await api.restore()) setInfo("Backup restored."); })}>Restore from backup…</button>
          <button className="btn btn-ghost text-red-700" onClick={wrap(async () => { if (confirm("Permanently delete ALL prospects, lists and saved searches?")) { await api.deleteAll(); setInfo("All prospect data deleted."); } })}>Delete all prospect data</button></div></section>
      <section className="card space-y-3 p-4">
        <h2 className="font-semibold">Southern Illinois territory</h2>
        <p className="text-sm text-slate-600">Enabled counties appear in Discover. Missouri's 114 counties and St. Louis City are always available.</p>
        <div className="flex flex-wrap gap-2">
          {regions.map((r) => (
            <span key={r.id} className={`flex items-center gap-1 rounded-full border px-2 py-1 text-xs ${r.enabled ? "border-brand-500 bg-brand-50" : "border-slate-300 text-slate-500"}`}>
              <button onClick={wrap(() => api.setRegionEnabled(r.id, !r.enabled))}>{r.enabled ? "✓" : "+"} {r.name}</button>
              {r.is_custom && <button title="Remove" onClick={wrap(() => api.removeCounty(r.id))}>×</button>}</span>))}
        </div>
        <div className="flex gap-2"><input className="input" placeholder="Add another Illinois county" value={newCounty} onChange={(e) => setNewCounty(e.target.value)} />
          <button className="btn btn-ghost" onClick={wrap(async () => { await api.addCounty("IL", newCounty); setNewCounty(""); })}>Add</button></div>
      </section>
      <section className="card space-y-3 p-4">
        <h2 className="font-semibold">Data provider credentials</h2>
        <p className="text-sm text-slate-600">Stored in Windows Credential Manager. Never written to the database or shown again after saving.</p>
        {PROVIDERS.map((p) => (
          <div key={p.id} className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-2">
            <div className="w-64"><div className="text-sm font-medium">{p.label}</div><div className="text-xs text-slate-500">{p.status}</div></div>
            <span className={`text-xs ${secrets[p.id] ? "text-emerald-700" : "text-slate-400"}`}>{secrets[p.id] ? "Saved" : "Not set"}</span>
            <input type="password" className="input w-64" placeholder="Paste key" value={vals[p.id] ?? ""} onChange={(e) => setVals({ ...vals, [p.id]: e.target.value })} />
            <button className="btn btn-primary" onClick={wrap(async () => { await api.setSecret(p.id, vals[p.id] ?? ""); setVals({ ...vals, [p.id]: "" }); })}>Save</button>
            {secrets[p.id] && <button className="btn btn-ghost" onClick={wrap(() => api.deleteSecret(p.id))}>Remove</button>}
          </div>))}
      </section>
    </div>
  );
}
