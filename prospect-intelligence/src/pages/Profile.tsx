import { useEffect, useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { api } from "../lib/api";
import { STAGES, VERTICALS, type Activity, type Contact, type Prospect } from "../lib/types";
import ScoreBadge from "../components/ScoreBadge";

export default function Profile({ id, onBack }: { id: number; onBack: () => void }) {
  const [p, setP] = useState<Prospect | null>(null);
  const [acts, setActs] = useState<Activity[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [note, setNote] = useState(""); const [action, setAction] = useState(""); const [due, setDue] = useState("");
  const [emp, setEmp] = useState("");
  const [c, setC] = useState({ name: "", title: "", email: "", phone: "" });
  const load = async () => {
    const [pp, a, k] = await Promise.all([api.prospect(id), api.activities(id), api.contacts(id)]);
    setP(pp); setActs(a); setContacts(k); setAction(pp.nextAction ?? ""); setDue(pp.nextActionDue ?? ""); setEmp(pp.employeesEst?.toString() ?? "");
  };
  useEffect(() => { load(); }, [id]);
  if (!p) return null;
  const patch = async (x: Partial<Prospect>) => { await api.save({ ...p, ...x }); load(); };
  const link = (u: string) => openUrl(/^https?:/.test(u) ? u : `https://${u}`);

  return (
    <div className="space-y-4">
      <button className="btn btn-ghost" onClick={onBack}>← Back</button>
      <div className="card flex items-start justify-between p-4">
        <div><h1 className="text-xl font-semibold text-brand-900">{p.name}</h1>
          <div className="text-sm text-slate-600">{[p.address, p.city, p.state, p.zip].filter(Boolean).join(", ") || "Address not available"}{p.county ? ` · ${p.county} County` : ""}</div>
          <div className="mt-1 flex flex-wrap gap-3 text-sm">
            {p.website && <button className="text-brand-600 underline" onClick={() => link(p.website!)}>{p.website}</button>}
            {p.phone && <span>{p.phone}</span>}
            {p.sourceUrl && <button className="text-slate-500 underline" onClick={() => link(p.sourceUrl!)}>Source: {p.source}</button>}</div></div>
        <div className="text-right"><div className="text-xs text-slate-500">Fit score</div><ScoreBadge score={p.score} /></div>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="card space-y-3 p-4">
          <h2 className="font-semibold">Qualification</h2>
          <label className="block text-sm">Stage<select className="input mt-1 block w-full capitalize" value={p.stage} onChange={async (e) => { await api.setStage(id, e.target.value); load(); }}>{STAGES.map((s) => <option key={s}>{s}</option>)}</select></label>
          <label className="block text-sm">Vertical<select className="input mt-1 block w-full capitalize" value={p.vertical} onChange={(e) => patch({ vertical: e.target.value })}>{VERTICALS.map((s) => <option key={s}>{s}</option>)}</select></label>
          <label className="block text-sm">Employees (enter only if verified)<input className="input mt-1 block w-full" value={emp} onChange={(e) => setEmp(e.target.value)}
            onBlur={() => patch({ employeesEst: emp ? +emp : null, employeesSource: emp ? "manual" : null })} /></label>
          <div><div className="text-sm font-medium">Why this fits</div>
            <ul className="mt-1 list-disc pl-5 text-sm text-slate-600">{p.scoreReasons.map((r) => <li key={r}>{r}</li>)}{p.scoreReasons.length === 0 && <li>No scoring signals yet</li>}</ul></div>
        </div>
        <div className="card space-y-3 p-4">
          <h2 className="font-semibold">Next action</h2>
          <input className="input w-full" placeholder="e.g. Call to discuss security assessment" value={action} onChange={(e) => setAction(e.target.value)} />
          <input type="date" className="input" value={due} onChange={(e) => setDue(e.target.value)} />
          <button className="btn btn-primary" onClick={async () => { await api.setNextAction(id, action || null, due || null); load(); }}>Save</button>
          <h2 className="pt-2 font-semibold">Log activity</h2>
          <textarea className="input w-full" rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
          <button className="btn btn-ghost" onClick={async () => { if (!note.trim()) return; await api.addActivity(id, "note", note.trim()); setNote(""); load(); }}>Add note</button>
        </div>
        <div className="card space-y-2 p-4">
          <h2 className="font-semibold">Contacts</h2>
          {contacts.map((k) => <div key={k.id} className="text-sm"><b>{k.name}</b>{k.title && ` — ${k.title}`}<div className="text-slate-500">{[k.email, k.phone].filter(Boolean).join(" · ")}{!k.verified && " (unverified)"}</div></div>)}
          {contacts.length === 0 && <p className="text-sm text-slate-500">No contacts yet. Add verified decision-makers manually or connect a contact provider in Settings.</p>}
          {(["name", "title", "email", "phone"] as const).map((f) => <input key={f} className="input w-full" placeholder={f} value={c[f]} onChange={(e) => setC({ ...c, [f]: e.target.value })} />)}
          <button className="btn btn-ghost" onClick={async () => { if (!c.name.trim()) return; await api.addContact({ prospectId: id, source: "manual", name: c.name, title: c.title || null, email: c.email || null, phone: c.phone || null }); setC({ name: "", title: "", email: "", phone: "" }); load(); }}>Add contact</button>
        </div>
      </div>
      <div className="card p-4"><h2 className="mb-2 font-semibold">Activity</h2>
        {acts.map((a) => <div key={a.id} className="border-t border-slate-100 py-1.5 text-sm"><span className="text-xs text-slate-500">{a.occurredAt} · {a.kind}</span><div>{a.summary}</div></div>)}
        {acts.length === 0 && <p className="text-sm text-slate-500">No activity yet.</p>}</div>
      <button className="text-sm text-red-600 underline" onClick={async () => { if (confirm("Delete this prospect and its history?")) { await api.remove(id); onBack(); } }}>Delete prospect</button>
    </div>
  );
}
