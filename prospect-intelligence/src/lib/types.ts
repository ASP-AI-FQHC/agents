export interface Prospect {
  id: number; name: string; website?: string | null; phone?: string | null; address?: string | null;
  city?: string | null; county?: string | null; state?: string | null; zip?: string | null;
  lat?: number | null; lon?: number | null; vertical: string; description?: string | null;
  employeesEst?: number | null; employeesSource?: string | null; source: string; sourceId?: string | null;
  sourceUrl?: string | null; stage: string; score: number; scoreReasons: string[];
  nextAction?: string | null; nextActionDue?: string | null;
}
export interface Region { id: number; state: string; name: string; kind: string; enabled: boolean; is_custom: boolean }
export interface Contact { id: number; prospectId: number; name: string; title?: string | null; email?: string | null;
  phone?: string | null; linkedin?: string | null; source: string; verified: boolean }
export interface Activity { id: number; prospectId: number; kind: string; summary: string; occurredAt: string }
export type Geography =
  | { kind: "state"; state: string }
  | { kind: "county"; state: string; county: string }
  | { kind: "city"; state: string; city: string }
  | { kind: "zip"; zip: string }
  | { kind: "radius"; query: string; miles: number };
export const STAGES = ["new", "researching", "qualified", "contacted", "meeting", "proposal", "won", "lost"] as const;
export const VERTICALS = ["manufacturing", "wholesale", "warehousing", "industrial", "transportation", "other"] as const;
