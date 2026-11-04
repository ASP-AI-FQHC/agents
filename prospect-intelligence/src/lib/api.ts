import { invoke } from "@tauri-apps/api/core";
import { open, save } from "@tauri-apps/plugin-dialog";
import type { Activity, Category, Conflict, Contact, Dashboard, FieldSource, Geography, Icp, ImportSummary, Prospect, Region, Signal } from "./types";

export interface Filter { text?: string; stage?: string; state?: string; county?: string; vertical?: string; minScore?: number; naics?: string[];
  tier?: string; origin?: string; listId?: number; minEmployees?: number; maxEmployees?: number }
type Draft = Partial<Prospect> & { name: string };
export const api = {
  regions: (state?: string) => invoke<Region[]>("list_regions", { state }),
  setRegionEnabled: (id: number, enabled: boolean) => invoke<void>("set_region_enabled", { id, enabled }),
  addCounty: (state: string, name: string) => invoke<void>("add_county", { state, name }),
  removeCounty: (id: number) => invoke<void>("remove_county", { id }),
  prospects: (filter: Filter) => invoke<Prospect[]>("list_prospects", { filter }),
  prospect: (id: number) => invoke<Prospect>("get_prospect", { id }),
  save: (p: Draft) => invoke<number>("save_prospect", { prospect: { id: 0, vertical: "other", source: "", origin: "", scoreReasons: [], score: 0, stage: "new", exportOk: true, tier: "Low", completeness: 0, ...p } }),
  remove: (id: number) => invoke<void>("delete_prospect", { id }),
  deleteAll: () => invoke<void>("delete_all_data"),
  setStage: (id: number, stage: string) => invoke<void>("set_stage", { id, stage }),
  setNextAction: (id: number, action: string | null, due: string | null) => invoke<void>("set_next_action", { id, action, due }),
  activities: (id: number) => invoke<Activity[]>("list_activities", { id }),
  addActivity: (id: number, kind: string, summary: string) => invoke<number>("add_activity", { id, kind, summary }),
  contacts: (id: number) => invoke<Contact[]>("list_contacts", { id }),
  addContact: (c: Partial<Contact> & { prospectId: number; name: string }) => invoke<number>("add_contact", { contact: { id: 0, source: "manual", verified: false, ...c } }),
  signals: (id: number) => invoke<Signal[]>("list_signals", { id }),
  addSignal: (s: Omit<Signal, "id">) => invoke<number>("add_signal", { signal: { id: 0, ...s } }),
  fieldSources: (id: number) => invoke<FieldSource[]>("field_sources", { id }),
  conflicts: (id: number) => invoke<Conflict[]>("conflicts", { id }),
  dashboard: () => invoke<Dashboard>("dashboard"),
  icp: () => invoke<Icp>("get_icp"),
  saveIcp: (icp: Icp) => invoke<void>("save_icp", { icp }),
  naics: () => invoke<Category[]>("naics_catalog"),
  lists: () => invoke<[number, string, number][]>("lists"),
  createList: (name: string) => invoke<number>("create_list", { name }),
  addToList: (list: number, ids: number[]) => invoke<void>("add_to_list", { list, ids }),
  deleteList: (list: number) => invoke<void>("delete_list", { list }),
  saveSearch: (name: string, kind: string, body: unknown) => invoke<number>("save_search", { name, kind, body: JSON.stringify(body) }),
  savedSearches: (kind: string) => invoke<[number, string, string][]>("saved_searches", { kind }),
  deleteSavedSearch: (id: number) => invoke<void>("delete_saved_search", { id }),
  addTask: (id: number, title: string, due: string | null) => invoke<number>("add_task", { id, title, due }),
  tasks: (id: number) => invoke<[number, string, string | null, boolean][]>("list_tasks", { id }),
  completeTask: (id: number, done: boolean) => invoke<void>("complete_task", { id, done }),
  search: (geography: Geography, verticals: string[], limit = 200) => invoke<Prospect[]>("search_companies", { request: { geography, verticals, limit } }),
  testConnection: (provider: string) => invoke<string>("test_connection", { provider }),
  secretStatus: () => invoke<[string, boolean][]>("secret_status"),
  setSecret: (name: string, value: string) => invoke<void>("set_secret", { name, value }),
  deleteSecret: (name: string) => invoke<void>("delete_secret", { name }),
  async importCsv(): Promise<ImportSummary | null> {
    const path = await open({ filters: [{ name: "CSV", extensions: ["csv"] }] });
    return typeof path === "string" ? invoke<ImportSummary>("import_csv", { path }) : null;
  },
  async exportCsv(ids: number[]): Promise<number | null> {
    const path = await save({ defaultPath: "prospects.csv", filters: [{ name: "CSV (Excel compatible)", extensions: ["csv"] }] });
    return path ? invoke<number>("export_csv", { ids, path }) : null;
  },
  async backup(): Promise<boolean> {
    const path = await save({ defaultPath: `prospect-intelligence-backup-${new Date().toISOString().slice(0, 10)}.db` });
    if (!path) return false; await invoke("backup_db", { path }); return true;
  },
  async restore(): Promise<boolean> {
    const path = await open({ filters: [{ name: "Backup", extensions: ["db"] }] });
    if (typeof path !== "string") return false; await invoke("restore_db", { path }); return true;
  },
};
