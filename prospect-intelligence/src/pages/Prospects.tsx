import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { STAGES, VERTICALS, type Prospect } from "../lib/types";
import ScoreBadge from "../components/ScoreBadge";

export default function Prospects({ onOpen }: { onOpen: (id: number) => void }) {
  const [rows, setRows] = useState<Prospect[]>([]);
  const [text, setText] = useState(""); const [stage, setStage] = useState(""); const [vertical, setVertical] = useState("");
  const [name, setName] = useState("");
  const load = () => api.prospects({ text: text || undefined, stage: stage || undefined, vertical: vertical || undefined }).then(setRows);
  useEffect(() => { load(); }, [text, stage, vertical]);
  const add = async () => { if (!name.trim()) return; const id = await api.save({ name: name.trim() }); setName(""); onOpen(id); };
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between"><h1 className="text-xl font-semibold text-brand-900">My Prospects</h1>
        <div className="flex gap-2"><input className="input" placeholder="Add company manually" value={name} onChange={(e) => setName(e.target.value)} />
          <button className="btn btn-primary" onClick={add}>Add</button></div></div>
      <div className="flex gap-2">
        <input className="input w-64" placeholder="Search saved prospects" value={text} onChange={(e) => setText(e.target.value)} />
        <select className="input" value={stage} onChange={(e) => setStage(e.target.value)}><option value="">All stages</option>{STAGES.map((s) => <option key={s}>{s}</option>)}</select>
        <select className="input" value={vertical} onChange={(e) => setVertical(e.target.value)}><option value="">All verticals</option>{VERTICALS.map((s) => <option key={s}>{s}</option>)}</select>
      </div>
      <div className="card overflow-hidden"><table className="w-full text-sm">
        <thead className="bg-slate-100 text-left text-xs uppercase text-slate-500"><tr><th className="p-2">Company</th><th>Vertical</th><th>Location</th><th>Stage</th><th>Next action</th><th>Fit</th></tr></thead>
        <tbody>{rows.map((p) => (
          <tr key={p.id} className="cursor-pointer border-t border-slate-100 hover:bg-brand-50" onClick={() => onOpen(p.id)}>
            <td className="p-2 font-medium">{p.name}</td><td className="capitalize">{p.vertical}</td>
            <td>{[p.city, p.state].filter(Boolean).join(", ") || "—"}</td><td className="capitalize">{p.stage}</td>
            <td>{p.nextAction ?? "—"}</td><td><ScoreBadge score={p.score} /></td></tr>))}
          {rows.length === 0 && <tr><td colSpan={6} className="p-6 text-center text-slate-500">No saved prospects yet. Use Discover to find companies.</td></tr>}
        </tbody></table></div>
    </div>
  );
}
