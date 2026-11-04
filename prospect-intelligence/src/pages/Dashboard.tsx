import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Dashboard as D } from "../lib/types";
import { Bars, Kpi } from "../components/Bits";

export default function Dashboard({ go }: { go: (p: string) => void }) {
  const [d, setD] = useState<D | null>(null);
  useEffect(() => { api.dashboard().then(setD); }, []);
  if (!d) return null;
  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold text-brand-900">Dashboard</h1>
      {d.total === 0 && <div className="card border-brand-500 bg-brand-50 p-4 text-sm">No prospects yet. Use <button className="font-semibold underline" onClick={() => go("Find Prospects")}>Find Prospects</button> to search public map data, or import an authorized CSV lead list. Nothing here is sample data.</div>}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        <Kpi label="Total prospects" value={d.total} onClick={() => go("Prospect Intelligence")} /><Kpi label="New" value={d.new} /><Kpi label="Priority A" value={d.priorityA} />
        <Kpi label="Qualified" value={d.qualified} /><Kpi label="Contacted" value={d.contacted} /><Kpi label="Meetings" value={d.meetings} /><Kpi label="Opportunities" value={d.opportunities} />
      </div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <Bars title="By state" data={d.byState} /><Bars title="By county (top 15)" data={d.byCounty} /><Bars title="By industry" data={d.byIndustry} />
        <Bars title="By employee range" data={d.bySize} /><Bars title="By priority tier" data={d.byTier} /><Bars title="By pipeline stage" data={d.byStage} />
      </div>
    </div>
  );
}
