import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Prospect } from "../lib/types";
import { Empty, Tier } from "../components/Bits";

export default function Lists({ onOpen }: { onOpen: (id: number) => void }) {
  const [lists, setLists] = useState<[number, string, number][]>([]);
  const [cur, setCur] = useState<number | null>(null); const [rows, setRows] = useState<Prospect[]>([]);
  const load = () => api.lists().then(setLists);
  useEffect(() => { load(); }, []);
  useEffect(() => { if (cur !== null) api.prospects({ listId: cur }).then(setRows); }, [cur]);
  return (
    <div className="space-y-4"><div className="flex items-center justify-between"><h1 className="text-xl font-semibold text-brand-900">My Lists</h1>
      <button className="btn btn-primary" onClick={async () => { const n = prompt("List name"); if (n) { await api.createList(n); load(); } }}>New list</button></div>
      <div className="grid gap-4 md:grid-cols-3">
        <div className="card divide-y divide-slate-100">{lists.map(([id, name, n]) => (
          <div key={id} className={`flex cursor-pointer items-center justify-between p-3 text-sm ${cur === id ? "bg-brand-50" : ""}`} onClick={() => setCur(id)}>
            <span>{name} <span className="text-slate-400">({n})</span></span>
            <button className="text-xs text-red-600" onClick={async (e) => { e.stopPropagation(); if (confirm(`Delete list "${name}"? Prospects are kept.`)) { await api.deleteList(id); setCur(null); setRows([]); load(); } }}>Delete</button></div>))}
          {lists.length === 0 && <Empty>No lists yet. Select prospects in Prospect Intelligence and choose “Add to list”.</Empty>}</div>
        <div className="card md:col-span-2">{cur === null ? <Empty>Select a list.</Empty> : (<>
          <div className="flex justify-end p-2"><button className="btn btn-ghost" disabled={!rows.length} onClick={() => api.exportCsv(rows.map((r) => r.id))}>Export list (CSV)</button></div>
          <table className="w-full text-sm"><tbody>{rows.map((p) => <tr key={p.id} className="cursor-pointer border-t border-slate-100 hover:bg-brand-50" onClick={() => onOpen(p.id)}><td className="p-2 font-medium">{p.name}</td><td>{p.city}</td><td><Tier tier={p.tier} /></td></tr>)}</tbody></table>
          {rows.length === 0 && <Empty>This list is empty.</Empty>}</>)}</div></div></div>
  );
}
