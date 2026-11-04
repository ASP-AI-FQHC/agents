import { invoke } from "@tauri-apps/api/core";
import type { Activity, Contact, Geography, Prospect, Region } from "./types";

export interface Filter { text?: string; stage?: string; state?: string; county?: string; vertical?: string; minScore?: number }
export const api = {
  regions: (state?: string) => invoke<Region[]>("list_regions", { state }),
  setRegionEnabled: (id: number, enabled: boolean) => invoke<void>("set_region_enabled", { id, enabled }),
  addCounty: (state: string, name: string) => invoke<void>("add_county", { state, name }),
  removeCounty: (id: number) => invoke<void>("remove_county", { id }),
  prospects: (filter: Filter) => invoke<Prospect[]>("list_prospects", { filter }),
  prospect: (id: number) => invoke<Prospect>("get_prospect", { id }),
  save: (prospect: Partial<Prospect> & { name: string }) =>
    invoke<number>("save_prospect", { prospect: { id: 0, vertical: "other", source: "", scoreReasons: [], score: 0, stage: "new", ...prospect } }),
  remove: (id: number) => invoke<void>("delete_prospect", { id }),
  setStage: (id: number, stage: string) => invoke<void>("set_stage", { id, stage }),
  setNextAction: (id: number, action: string | null, due: string | null) => invoke<void>("set_next_action", { id, action, due }),
  activities: (id: number) => invoke<Activity[]>("list_activities", { id }),
  addActivity: (id: number, kind: string, summary: string) => invoke<number>("add_activity", { id, kind, summary }),
  contacts: (id: number) => invoke<Contact[]>("list_contacts", { id }),
  addContact: (contact: Omit<Contact, "id" | "verified"> & { verified?: boolean }) =>
    invoke<number>("add_contact", { contact: { id: 0, verified: false, ...contact } }),
  search: (geography: Geography, verticals: string[], limit = 200) =>
    invoke<Prospect[]>("search_companies", { request: { geography, verticals, limit } }),
  secretStatus: () => invoke<[string, boolean][]>("secret_status"),
  setSecret: (name: string, value: string) => invoke<void>("set_secret", { name, value }),
  deleteSecret: (name: string) => invoke<void>("delete_secret", { name }),
};
