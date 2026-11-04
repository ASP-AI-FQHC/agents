import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Prospect } from "../lib/types";
import { Tier } from "../components/Bits";

export default function Reports() {
  const [rows, setRows] = useState<Prospect[]>([]); const [msg, setMsg] = useState("");
  useEffect(() => { api.prospects({}).then(setRows); }, []);
  return (
    <div className="space-y-4"><div className="flex items-center justify-between print:hidden"><h1 className="text-xl font-semibold text-brand-900">Reports</h1>
      <div className="flex gap-2"><button className="btn btn-ghost" onClick={() => window.print()}>Print / Save as PDF</button>
        <button className="btn btn-primary" onClick={async () => { const s = await api.exportCsv(rows.map((r) => r.id)); if (s !== null) setMsg(`Exported ${rows.length - s} record(s)${s ? `, ${s} withheld by license` : ""}.`); }}>Export all (CSV)</button></div></div>
      {msg && <div className="rounded bg-emerald-50 p-2 text-sm">{msg}</div>}
      <div className="card p-4"><div className="mb-2 flex items-baseline justify-between"><h2 className="font-semibold">Lead score report</h2><span className="text-xs text-slate-500">Generated {new Date().toLocaleDateString()}</span></div>
        <table className="w-full text-sm"><thead className="text-left text-xs uppercase text-slate-500"><tr><th>Company</th><th>Tier</th><th>Score</th><th>Completeness</th><th>Stage</th></tr></thead>
          <tbody>{rows.map((p) => <tr key={p.id} className="border-t border-slate-100"><td className="py-1">{p.name}</td><td><Tier tier={p.tier} /></td><td>{p.score}</td><td>{p.completeness}%</td><td className="capitalize">{p.stage.replace(/_/g, " ")}</td></tr>)}</tbody></table>
        {rows.length === 0 && <p className="p-4 text-center text-sm text-slate-500">No prospects to report on yet.</p>}</div>
    </div>
  );
}
