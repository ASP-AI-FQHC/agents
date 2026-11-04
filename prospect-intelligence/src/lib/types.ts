export interface Prospect {
  id: number; name: string; website?: string | null; phone?: string | null; address?: string | null;
  city?: string | null; county?: string | null; state?: string | null; zip?: string | null;
  lat?: number | null; lon?: number | null; vertical: string; description?: string | null;
  employeesEst?: number | null; employeesSource?: string | null; source: string; sourceId?: string | null;
  sourceUrl?: string | null; stage: string; score: number; scoreReasons: string[];
  nextAction?: string | null; nextActionDue?: string | null;
  legalName?: string | null; naics?: string | null; sic?: string | null; revenueRange?: string | null; yearEstablished?: number | null;
  parentCompany?: string | null; facilities?: number | null; products?: string | null; owner?: string | null; tags?: string | null;
  origin: string; exportOk: boolean; tier: string; completeness: number; updatedAt?: string | null;
}
export interface Region { id: number; state: string; name: string; kind: string; enabled: boolean; is_custom: boolean }
export interface Contact { id: number; prospectId: number; name: string; title?: string | null; email?: string | null;
  phone?: string | null; linkedin?: string | null; source: string; verified: boolean; profileUrl?: string | null; verifiedOn?: string | null }
export interface Activity { id: number; prospectId: number; kind: string; summary: string; occurredAt: string }
export type Geography =
  | { kind: "state"; state: string }
  | { kind: "county"; state: string; county: string }
  | { kind: "city"; state: string; city: string }
  | { kind: "zip"; zip: string }
  | { kind: "radius"; query: string; miles: number };
export const STAGES = ["new", "researching", "qualified", "contacted", "meeting", "opportunity", "proposal", "won", "lost", "not_a_fit"] as const;
export const VERTICALS = ["manufacturing", "wholesale", "warehousing", "industrial", "transportation", "other"] as const;
export interface Signal { id: number; prospectId: number; kind: string; summary: string; source: string; sourceUrl?: string | null; eventDate?: string | null }
export interface FieldSource { id: number; prospectId: number; field: string; value: string; source: string; sourceRef?: string | null; retrievedAt: string; status: string; confidence?: string | null }
export interface Conflict { field: string; values: FieldSource[] }
export interface Category { id: string; label: string; group: string; prefixes: string[] }
export interface Dashboard { total: number; new: number; priorityA: number; qualified: number; contacted: number; meetings: number; opportunities: number;
  byState: [string, number][]; byCounty: [string, number][]; byIndustry: [string, number][]; bySize: [string, number][]; byTier: [string, number][]; byStage: [string, number][] }
export interface Icp { weights: Record<"industry" | "size" | "geo" | "complexity" | "signals" | "services" | "contact", number>;
  tier_a: number; tier_b: number; tier_c: number; min_employees: number; max_employees: number; states: string[]; counties: string[] }
export interface ImportSummary { inserted: number; merged: number; skipped: number; errors: string[] }
