export default function ScoreBadge({ score }: { score: number }) {
  const c = score >= 70 ? "bg-emerald-100 text-emerald-800" : score >= 40 ? "bg-amber-100 text-amber-800" : "bg-slate-100 text-slate-600";
  return <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${c}`}>{score}</span>;
}
