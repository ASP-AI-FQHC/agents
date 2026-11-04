import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { STAGES, type Prospect } from "../lib/types";
import { Tier } from "../components/Bits";

export default function Pipeline({ onOpen }: { onOpen: (id: number) => void }) {
  const [rows, setRows] = useState<Prospect[]>([]);
  const load = () => api.prospects({}).then(setRows);
  useEffect(() => { load(); }, []);
  return (
    <div className="space-y-3"><h1 className="text-xl font-semibold text-brand-900">Sales Pipeline</h1>
      <div className="flex gap-3 overflow-x-auto pb-2">{STAGES.map((s) => { const col = rows.filter((r) => r.stage === s); return (
        <div key={s} className="w-60 shrink-0 rounded-lg bg-slate-100 p-2"><div className="mb-2 flex justify-between text-xs font-semibold uppercase text-slate-600"><span>{s.replace(/_/g, " ")}</span><span>{col.length}</span></div>
          <div className="space-y-2">{col.map((p) => (
            <div key={p.id} className="card p-2 text-sm"><button className="text-left font-medium text-brand-700 hover:underline" onClick={() => onOpen(p.id)}>{p.name}</button>
              <div className="mt-1 flex items-center justify-between"><Tier tier={p.tier} />
                <select aria-label="Move stage" className="rounded border border-slate-200 text-xs" value={p.stage} onChange={async (e) => { await api.setStage(p.id, e.target.value); load(); }}>{STAGES.map((x) => <option key={x}>{x}</option>)}</select></div></div>))}</div></div>); })}</div></div>
  );
}
