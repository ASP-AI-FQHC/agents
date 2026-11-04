import { useEffect, useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { api } from "../lib/api";
import { outreach, opportunities } from "../lib/outreach";
import { STAGES, VERTICALS, type Activity, type Conflict, type Contact, type FieldSource, type Prospect, type Signal } from "../lib/types";
import { NA, Origin, Tier } from "../components/Bits";

const Sec = ({ t, children }: { t: string; children: React.ReactNode }) => <section className="card p-4 break-inside-avoid"><h2 className="mb-2 font-semibold text-brand-900">{t}</h2>{children}</section>;

export default function Profile({ id, onBack }: { id: number; onBack: () => void }) {
  const [p, setP] = useState<Prospect | null>(null);
  const [acts, setActs] = useState<Activity[]>([]); const [contacts, setContacts] = useState<Contact[]>([]); const [signals, setSignals] = useState<Signal[]>([]);
  const [conf, setConf] = useState<Conflict[]>([]); const [src, setSrc] = useState<FieldSource[]>([]); const [tasks, setTasks] = useState<[number, string, string | null, boolean][]>([]);
  const [note, setNote] = useState(""); const [action, setAction] = useState(""); const [due, setDue] = useState(""); const [task, setTask] = useState("");
  const [c, setC] = useState({ name: "", title: "", email: "", phone: "", verified: false, profileUrl: "" });
  const [sg, setSg] = useState({ kind: "expansion", summary: "", source: "", sourceUrl: "", eventDate: "" });
  const [copied, setCopied] = useState("");
  const load = async () => {
    const [pp, a, k, s, cf, fs, t] = await Promise.all([api.prospect(id), api.activities(id), api.contacts(id), api.signals(id), api.conflicts(id), api.fieldSources(id), api.tasks(id)]);
    setP(pp); setActs(a); setContacts(k); setSignals(s); setConf(cf); setSrc(fs); setTasks(t); setAction(pp.nextAction ?? ""); setDue(pp.nextActionDue ?? "");
  };
  useEffect(() => { load(); }, [id]);
  if (!p) return null;
  const patch = async (x: Partial<Prospect>) => { await api.save({ ...p, ...x }); load(); };
  const link = (u: string) => openUrl(/^https?:/.test(u) ? u : `https://${u}`);
  const o = outreach(p, contacts); const opps = opportunities(p, signals.length);
  const copy = async (k: string, t: string) => { await navigator.clipboard.writeText(t); setCopied(k); setTimeout(() => setCopied(""), 1500); };
  const num = (v: string) => (v.trim() === "" ? null : Number.isFinite(+v) ? +v : null);
  const Field = ({ label, k, type = "text" }: { label: string; k: keyof Prospect; type?: string }) => (
    <label className="block text-sm">{label}<input type={type} className="input mt-0.5 block w-full" defaultValue={(p[k] as string | number | null) ?? ""}
      onBlur={(e) => { const v = e.target.value; const cur = (p[k] as string | number | null) ?? ""; if (String(cur) !== v) patch({ [k]: type === "number" ? num(v) : v || null, ...(k === "employeesEst" ? { employeesSource: v ? "manual entry" : null } : {}) } as Partial<Prospect>); }} /></label>);
  const Mail = ({ k, subject, body }: { k: string; subject: string; body: string }) => (
    <div className="rounded border border-slate-200 p-2 text-sm"><div className="font-medium">Subject: {subject}</div><pre className="my-1 whitespace-pre-wrap font-sans text-slate-700">{body}</pre>
      <div className="flex gap-2 print:hidden"><button className="btn btn-ghost" onClick={() => copy(k, `Subject: ${subject}\n\n${body}`)}>{copied === k ? "Copied" : "Copy"}</button>
        <button className="btn btn-ghost" onClick={() => openUrl(o.mailto(subject, body))}>Open in email client</button></div></div>);

  return (
    <div className="space-y-4">
      <div className="flex justify-between print:hidden"><button className="btn btn-ghost" onClick={onBack}>← Back</button><button className="btn btn-primary" onClick={() => window.print()}>Print / Save PDF briefing</button></div>
      <div className="hidden items-center justify-between border-b pb-2 print:flex"><b>IT Architechs — Prospect Briefing</b><span>Generated {new Date().toLocaleDateString()}</span></div>
      <header className="card flex items-start justify-between p-4">
        <div><h1 className="text-xl font-semibold text-brand-900">{p.name} <Origin o={p.origin} /></h1>
          <div className="text-sm text-slate-600 capitalize">{p.vertical}{p.naics ? ` · NAICS ${p.naics}` : " · NAICS not verified"} · {[p.city, p.state].filter(Boolean).join(", ") || "Location not available"}</div>
          <div className="mt-1 flex flex-wrap gap-3 text-sm">{p.website ? <button className="text-brand-600 underline" onClick={() => link(p.website!)}>{p.website}</button> : <span className="text-slate-400">Website not available</span>}<NA v={p.phone} /></div></div>
        <div className="text-right"><Tier tier={p.tier} /><div className="text-2xl font-semibold">{p.score}<span className="text-sm text-slate-400">/100</span></div><div className="text-xs text-slate-500">Data completeness {p.completeness}%</div>
          <select aria-label="Stage" className="input mt-1 capitalize print:hidden" value={p.stage} onChange={async (e) => { await api.setStage(id, e.target.value); load(); }}>{STAGES.map((s) => <option key={s}>{s}</option>)}</select></div>
      </header>

      <div className="grid gap-4 lg:grid-cols-2">
        <Sec t="A. Company overview">
          <div className="grid grid-cols-2 gap-2 print:hidden">
            <Field label="Legal name" k="legalName" /><Field label="Website" k="website" /><Field label="Phone" k="phone" /><Field label="Address" k="address" />
            <Field label="NAICS" k="naics" /><Field label="SIC" k="sic" /><Field label="Employees (only if sourced)" k="employeesEst" type="number" /><Field label="Revenue range" k="revenueRange" />
            <Field label="Facilities" k="facilities" type="number" /><Field label="Year established" k="yearEstablished" type="number" /><Field label="Parent company" k="parentCompany" /><Field label="Owner (salesperson)" k="owner" />
            <label className="col-span-2 block text-sm">Products / description<textarea className="input mt-0.5 block w-full" rows={2} defaultValue={p.description ?? ""} onBlur={(e) => patch({ description: e.target.value || null })} /></label>
            <label className="text-sm">Vertical<select className="input mt-0.5 block w-full capitalize" value={p.vertical} onChange={(e) => patch({ vertical: e.target.value })}>{VERTICALS.map((v) => <option key={v}>{v}</option>)}</select></label>
            <Field label="Tags (comma separated)" k="tags" /></div>
          <dl className="hidden grid-cols-2 gap-1 text-sm print:grid">{([["Legal name", p.legalName], ["Address", p.address], ["Employees", p.employeesEst], ["Revenue", p.revenueRange], ["Facilities", p.facilities], ["Description", p.description]] as const).map(([k, v]) => <div key={k}><dt className="text-xs text-slate-500">{k}</dt><dd><NA v={v} /></dd></div>)}</dl>
        </Sec>
        <Sec t="Fit score explanation"><ul className="space-y-1 text-sm">{p.scoreReasons.map((r) => <li key={r}>{r}</li>)}</ul>
          <p className="mt-2 text-xs text-slate-500">Unverified or missing data never earns full points. Edit weights in Settings → Ideal Customer Profile.</p></Sec>

        <Sec t="B. Leadership and contacts">
          {contacts.map((k) => <div key={k.id} className="mb-1 text-sm"><b>{k.name}</b>{k.title && ` — ${k.title}`} <span className={`rounded px-1 text-xs ${k.verified ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"}`}>{k.verified ? `Verified ${k.verifiedOn ?? ""}` : "Unverified lead"}</span>
            <div className="text-slate-500">{[k.email, k.phone].filter(Boolean).join(" · ") || "No direct contact info"} · source: {k.source}{k.profileUrl && <> · <button className="underline" onClick={() => link(k.profileUrl!)}>profile</button></>}</div></div>)}
          {contacts.length === 0 && <p className="text-sm text-slate-500">No decision-makers on file. Add only contacts you can source; none are inferred.</p>}
          <div className="mt-2 grid grid-cols-2 gap-1 print:hidden">{(["name", "title", "email", "phone", "profileUrl"] as const).map((f) => <input key={f} className="input" placeholder={f === "profileUrl" ? "public profile URL" : f} value={c[f]} onChange={(e) => setC({ ...c, [f]: e.target.value })} />)}
            <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={c.verified} onChange={(e) => setC({ ...c, verified: e.target.checked })} />I verified this</label>
            <button className="btn btn-ghost" onClick={async () => { if (!c.name.trim()) return; await api.addContact({ prospectId: id, name: c.name, title: c.title || null, email: c.email || null, phone: c.phone || null, profileUrl: c.profileUrl || null, verified: c.verified, source: "manual entry" }); setC({ name: "", title: "", email: "", phone: "", verified: false, profileUrl: "" }); load(); }}>Add contact</button></div>
        </Sec>

        <Sec t="C. Business intelligence — sourced signals">
          {signals.map((s) => <div key={s.id} className="mb-1 text-sm"><b className="capitalize">{s.kind}</b>: {s.summary}<div className="text-xs text-slate-500">{s.eventDate ?? "date n/a"} · {s.source}{s.sourceUrl && <> · <button className="underline" onClick={() => link(s.sourceUrl!)}>link</button></>}</div></div>)}
          {signals.length === 0 && <p className="text-sm text-slate-500">No sourced signals. Every signal needs a source.</p>}
          <div className="mt-2 grid grid-cols-2 gap-1 print:hidden"><select className="input" value={sg.kind} onChange={(e) => setSg({ ...sg, kind: e.target.value })}>{["expansion", "new facility", "hiring", "new technology leader", "ERP project", "acquisition", "technology incident"].map((k) => <option key={k}>{k}</option>)}</select>
            <input type="date" className="input" value={sg.eventDate} onChange={(e) => setSg({ ...sg, eventDate: e.target.value })} />
            <input className="input col-span-2" placeholder="What happened" value={sg.summary} onChange={(e) => setSg({ ...sg, summary: e.target.value })} />
            <input className="input" placeholder="Source name (required)" value={sg.source} onChange={(e) => setSg({ ...sg, source: e.target.value })} /><input className="input" placeholder="Source URL" value={sg.sourceUrl} onChange={(e) => setSg({ ...sg, sourceUrl: e.target.value })} />
            <button className="btn btn-ghost" onClick={async () => { if (!sg.summary.trim() || !sg.source.trim()) return; await api.addSignal({ prospectId: id, kind: sg.kind, summary: sg.summary, source: sg.source, sourceUrl: sg.sourceUrl || null, eventDate: sg.eventDate || null }); setSg({ ...sg, summary: "", source: "", sourceUrl: "" }); load(); }}>Add signal</button></div>
        </Sec>

        <Sec t="D. IT Architechs opportunity analysis (sales hypotheses)">
          {opps.map((x) => <div key={x.service} className="mb-1 text-sm"><b>{x.service}</b><div className="text-xs text-slate-500">Basis (documented): {x.basis}</div></div>)}
          {opps.length === 0 && <p className="text-sm text-slate-500">Not enough documented facts to suggest opportunities. These are hypotheses, not findings; no vulnerabilities are asserted.</p>}
        </Sec>
      </div>

      <Sec t="E. Sales approach (template-generated from stored facts — not AI, not sent automatically)">
        <p className="text-sm"><b>Recommended contact:</b> {o.recipient}</p>
        <div className="mt-2 grid gap-3 lg:grid-cols-2">
          <Mail k="intro" subject={o.intro.subject} body={o.intro.body} /><Mail k="exec" subject={o.executive.subject} body={o.executive.body} />
          <Mail k="follow" subject={o.followUp.subject} body={o.followUp.body} />
          <div className="space-y-2 text-sm"><div><b>Phone script</b><p className="text-slate-700">{o.phone}</p><button className="btn btn-ghost print:hidden" onClick={() => copy("ph", o.phone)}>{copied === "ph" ? "Copied" : "Copy"}</button></div>
            <div><b>Voicemail</b><p className="text-slate-700">{o.voicemail}</p><button className="btn btn-ghost print:hidden" onClick={() => copy("vm", o.voicemail)}>{copied === "vm" ? "Copied" : "Copy"}</button></div>
            <div><b>Discovery questions</b><ol className="list-decimal pl-5">{o.questions.map((q) => <li key={q}>{q}</li>)}</ol></div></div></div>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 print:hidden"><input className="input" placeholder="Next action" value={action} onChange={(e) => setAction(e.target.value)} /><div className="flex gap-2"><input type="date" className="input" value={due} onChange={(e) => setDue(e.target.value)} />
          <button className="btn btn-primary" onClick={async () => { await api.setNextAction(id, action || null, due || null); load(); }}>Save next action</button></div></div>
        {p.nextAction && <p className="hidden text-sm print:block"><b>Next action:</b> {p.nextAction} {p.nextActionDue}</p>}
      </Sec>

      <div className="grid gap-4 lg:grid-cols-2">
        <Sec t="F. Sales activity">
          <div className="flex gap-2 print:hidden"><input className="input flex-1" placeholder="Log a note or contact attempt" value={note} onChange={(e) => setNote(e.target.value)} />
            <button className="btn btn-ghost" onClick={async () => { if (note.trim()) { await api.addActivity(id, "note", note.trim()); setNote(""); load(); } }}>Add</button></div>
          <div className="mt-2 flex gap-2 print:hidden"><input className="input flex-1" placeholder="New task" value={task} onChange={(e) => setTask(e.target.value)} />
            <button className="btn btn-ghost" onClick={async () => { if (task.trim()) { await api.addTask(id, task.trim(), null); setTask(""); load(); } }}>Add task</button></div>
          {tasks.map(([tid, t, d, done]) => <label key={tid} className="mt-1 flex items-center gap-2 text-sm"><input type="checkbox" checked={done} onChange={async (e) => { await api.completeTask(tid, e.target.checked); load(); }} />{t}{d && <span className="text-xs text-slate-500">due {d}</span>}</label>)}
          {acts.map((a) => <div key={a.id} className="border-t border-slate-100 py-1 text-sm"><span className="text-xs text-slate-500">{a.occurredAt} · {a.kind}</span><div>{a.summary}</div></div>)}
        </Sec>
        <Sec t="G. Sources & conflicts">
          {conf.length > 0 && <div className="mb-2 rounded bg-amber-50 p-2 text-sm"><b>Conflicting values — review:</b>{conf.map((x) => <div key={x.field}><span className="capitalize">{x.field}</span>: {x.values.map((v) => `${v.value} (${v.source})`).join(" vs ")}</div>)}</div>}
          <table className="w-full text-xs"><thead className="text-left text-slate-500"><tr><th>Field</th><th>Value</th><th>Source</th><th>Retrieved</th><th>Status</th></tr></thead>
            <tbody>{src.map((s) => <tr key={s.id} className="border-t border-slate-100"><td>{s.field}</td><td>{s.value}</td><td>{s.sourceRef?.startsWith("http") ? <button className="underline" onClick={() => link(s.sourceRef!)}>{s.source}</button> : s.source}</td><td>{s.retrievedAt.slice(0, 10)}</td><td>{s.status}{s.confidence ? ` / ${s.confidence}` : ""}</td></tr>)}</tbody></table>
          {src.length === 0 && <p className="text-sm text-slate-500">No sourced fields recorded.</p>}
          {p.sourceUrl && <p className="mt-2 text-xs">Record origin: <button className="underline" onClick={() => link(p.sourceUrl!)}>{p.source}</button></p>}
        </Sec>
      </div>
      <button className="text-sm text-red-600 underline print:hidden" onClick={async () => { if (confirm("Delete this prospect and its history?")) { await api.remove(id); onBack(); } }}>Delete prospect</button>
    </div>
  );
}
