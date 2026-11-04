import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Region } from "../lib/types";

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
  const [newCounty, setNewCounty] = useState(""); const [err, setErr] = useState("");
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
