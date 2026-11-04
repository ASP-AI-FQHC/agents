import type { ReactNode } from "react";
export const NA = ({ v, verified }: { v: ReactNode; verified?: boolean }) =>
  v === null || v === undefined || v === "" ? <span className="text-slate-400">Not available</span> : <>{v}{verified === false && <span className="ml-1 text-xs text-amber-600">(not verified)</span>}</>;
export function Tier({ tier }: { tier: string }) {
  const c = tier === "A" ? "bg-emerald-100 text-emerald-800" : tier === "B" ? "bg-sky-100 text-sky-800" : tier === "C" ? "bg-amber-100 text-amber-800" : "bg-slate-100 text-slate-600";
  return <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${c}`}>{tier === "Low" ? "Low" : `Priority ${tier}`}</span>;
}
export const Origin = ({ o }: { o: string }) => <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">{({ live: "Live (OSM)", imported: "Imported", manual: "Manual", demo: "Demo" } as Record<string, string>)[o] ?? o}</span>;
export const Empty = ({ children }: { children: ReactNode }) => <div className="p-8 text-center text-sm text-slate-500">{children}</div>;
export const Kpi = ({ label, value, onClick }: { label: string; value: number; onClick?: () => void }) =>
  <button onClick={onClick} className="card p-4 text-left hover:border-brand-500"><div className="text-3xl font-semibold text-brand-900">{value}</div><div className="text-xs uppercase tracking-wide text-slate-500">{label}</div></button>;
export function Bars({ title, data }: { title: string; data: [string, number][] }) {
  const max = Math.max(1, ...data.map((d) => d[1]));
  return (<div className="card p-4"><h3 className="mb-2 text-sm font-semibold">{title}</h3>
    {data.length === 0 ? <p className="text-xs text-slate-400">No data yet</p> : data.map(([k, v]) => (
      <div key={k} className="mb-1 flex items-center gap-2 text-xs"><span className="w-28 shrink-0 truncate capitalize" title={k}>{k}</span>
        <div className="h-3 flex-1 rounded bg-slate-100"><div className="h-3 rounded bg-brand-500" style={{ width: `${(v / max) * 100}%` }} /></div><span className="w-8 text-right">{v}</span></div>))}</div>);
}
