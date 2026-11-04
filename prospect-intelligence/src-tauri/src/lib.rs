mod osm;
mod secrets;

use pi_core::{db, geo, models::*, repo};
use rusqlite::Connection;
use std::sync::Mutex;
use tauri::{Manager, State};

struct Db(Mutex<Connection>);
type R<T> = Result<T, String>;
fn e<E: std::fmt::Display>(x: E) -> String { x.to_string() }

fn lock<'a>(s: &'a State<Db>) -> R<std::sync::MutexGuard<'a, Connection>> { s.0.lock().map_err(e) }

#[tauri::command] fn list_regions(s: State<Db>, state: Option<String>) -> R<Vec<Region>> { geo::list_regions(&*lock(&s)?, state.as_deref()).map_err(e) }
#[tauri::command] fn set_region_enabled(s: State<Db>, id: i64, enabled: bool) -> R<()> { geo::set_enabled(&*lock(&s)?, id, enabled).map_err(e) }
#[tauri::command] fn add_county(s: State<Db>, state: String, name: String) -> R<()> { geo::add_county(&*lock(&s)?, &state, &name).map_err(e) }
#[tauri::command] fn remove_county(s: State<Db>, id: i64) -> R<()> { geo::remove_county(&*lock(&s)?, id).map_err(e) }

#[tauri::command] fn list_prospects(s: State<Db>, filter: repo::Filter) -> R<Vec<Prospect>> { repo::list_prospects(&*lock(&s)?, &filter).map_err(e) }
#[tauri::command] fn get_prospect(s: State<Db>, id: i64) -> R<Prospect> { repo::get_prospect(&*lock(&s)?, id).map_err(e) }
/// id == 0 → create/merge (deduplicated); id > 0 → manual edit.
#[tauri::command] fn save_prospect(s: State<Db>, mut prospect: Prospect) -> R<i64> {
    let c = &*lock(&s)?;
    if prospect.id > 0 { repo::update_prospect(c, &prospect).map_err(e)?; return Ok(prospect.id); }
    if prospect.source.is_empty() { prospect.source = "manual".into(); }
    if prospect.origin.is_empty() { prospect.origin = "manual".into(); }
    repo::ingest(c, prospect).map(|i| i.id).map_err(e)
}
#[tauri::command] fn delete_prospect(s: State<Db>, id: i64) -> R<()> { repo::delete_prospect(&*lock(&s)?, id).map_err(e) }
#[tauri::command] fn delete_all_data(s: State<Db>) -> R<()> { repo::delete_all_data(&*lock(&s)?).map_err(e) }
#[tauri::command] fn set_stage(s: State<Db>, id: i64, stage: String) -> R<()> { repo::set_stage(&*lock(&s)?, id, &stage).map_err(e) }
#[tauri::command] fn set_next_action(s: State<Db>, id: i64, action: Option<String>, due: Option<String>) -> R<()> {
    repo::set_next_action(&*lock(&s)?, id, action.as_deref(), due.as_deref()).map_err(e) }
#[tauri::command] fn list_activities(s: State<Db>, id: i64) -> R<Vec<Activity>> { repo::list_activities(&*lock(&s)?, id).map_err(e) }
#[tauri::command] fn add_activity(s: State<Db>, id: i64, kind: String, summary: String) -> R<i64> { repo::add_activity(&*lock(&s)?, id, &kind, &summary).map_err(e) }
#[tauri::command] fn list_contacts(s: State<Db>, id: i64) -> R<Vec<Contact>> { repo::list_contacts(&*lock(&s)?, id).map_err(e) }
#[tauri::command] fn add_contact(s: State<Db>, contact: Contact) -> R<i64> { repo::add_contact(&*lock(&s)?, &contact).map_err(e) }
#[tauri::command] fn list_signals(s: State<Db>, id: i64) -> R<Vec<Signal>> { repo::list_signals(&*lock(&s)?, id).map_err(e) }
#[tauri::command] fn add_signal(s: State<Db>, signal: Signal) -> R<i64> { repo::add_signal(&*lock(&s)?, &signal).map_err(e) }
#[tauri::command] fn field_sources(s: State<Db>, id: i64) -> R<Vec<FieldSource>> { repo::field_sources(&*lock(&s)?, id).map_err(e) }
#[tauri::command] fn conflicts(s: State<Db>, id: i64) -> R<Vec<Conflict>> { repo::conflicts(&*lock(&s)?, id).map_err(e) }
#[tauri::command] fn dashboard(s: State<Db>) -> R<repo::Dashboard> { repo::dashboard(&*lock(&s)?).map_err(e) }
#[tauri::command] fn get_icp(s: State<Db>) -> R<pi_core::icp::Icp> { repo::load_icp(&*lock(&s)?).map_err(e) }
#[tauri::command] fn save_icp(s: State<Db>, icp: pi_core::icp::Icp) -> R<()> { repo::save_icp(&*lock(&s)?, &icp).map_err(e) }
#[tauri::command] fn naics_catalog() -> Vec<pi_core::naics::Category> { pi_core::naics::SECTORS.iter().chain(pi_core::naics::CATEGORIES).cloned().collect() }
#[tauri::command] fn lists(s: State<Db>) -> R<Vec<(i64, String, i64)>> { repo::lists(&*lock(&s)?).map_err(e) }
#[tauri::command] fn create_list(s: State<Db>, name: String) -> R<i64> { repo::create_list(&*lock(&s)?, &name).map_err(e) }
#[tauri::command] fn add_to_list(s: State<Db>, list: i64, ids: Vec<i64>) -> R<()> { repo::add_to_list(&*lock(&s)?, list, &ids).map_err(e) }
#[tauri::command] fn delete_list(s: State<Db>, list: i64) -> R<()> { repo::delete_list(&*lock(&s)?, list).map_err(e) }
#[tauri::command] fn save_search(s: State<Db>, name: String, kind: String, body: String) -> R<i64> { repo::save_search(&*lock(&s)?, &name, &kind, &body).map_err(e) }
#[tauri::command] fn saved_searches(s: State<Db>, kind: String) -> R<Vec<(i64, String, String)>> { repo::saved_searches(&*lock(&s)?, &kind).map_err(e) }
#[tauri::command] fn delete_saved_search(s: State<Db>, id: i64) -> R<()> { repo::delete_saved_search(&*lock(&s)?, id).map_err(e) }
#[tauri::command] fn add_task(s: State<Db>, id: i64, title: String, due: Option<String>) -> R<i64> { repo::add_task(&*lock(&s)?, id, &title, due.as_deref()).map_err(e) }
#[tauri::command] fn list_tasks(s: State<Db>, id: i64) -> R<Vec<(i64, String, Option<String>, bool)>> { repo::list_tasks(&*lock(&s)?, id).map_err(e) }
#[tauri::command] fn complete_task(s: State<Db>, id: i64, done: bool) -> R<()> { repo::complete_task(&*lock(&s)?, id, done).map_err(e) }

/// Import authorized lead lists from a CSV file chosen by the user.
#[tauri::command] fn import_csv(s: State<Db>, path: String) -> R<pi_core::csvio::ImportSummary> {
    let data = std::fs::read_to_string(&path).map_err(e)?;
    let name = std::path::Path::new(&path).file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
    pi_core::csvio::import(&*lock(&s)?, &data, &name, None).map_err(e)
}
/// Export the given prospects to CSV (Excel-compatible). Returns how many were withheld for license reasons.
#[tauri::command] fn export_csv(s: State<Db>, ids: Vec<i64>, path: String) -> R<usize> {
    let c = &*lock(&s)?;
    let rows: Vec<Prospect> = ids.iter().filter_map(|i| repo::get_prospect(c, *i).ok()).collect();
    let (body, skipped) = pi_core::csvio::export(&rows);
    std::fs::write(&path, body).map_err(e)?;
    Ok(skipped)
}
/// Consistent online snapshot of the database (VACUUM INTO).
#[tauri::command] fn backup_db(s: State<Db>, path: String) -> R<()> {
    let _ = std::fs::remove_file(&path);
    lock(&s)?.execute("VACUUM INTO ?1", [path]).map(|_| ()).map_err(e)
}
#[tauri::command] fn restore_db(app: tauri::AppHandle, s: State<Db>, path: String) -> R<()> {
    let probe = Connection::open(&path).map_err(e)?;
    let ok: String = probe.query_row("PRAGMA integrity_check", [], |r| r.get(0)).map_err(e)?;
    let has: i64 = probe.query_row("SELECT COUNT(*) FROM sqlite_master WHERE name='prospects'", [], |r| r.get(0)).map_err(e)?;
    if ok != "ok" || has == 0 { return Err("not a valid Prospect Intelligence backup".into()); }
    drop(probe);
    let mut guard = s.0.lock().map_err(e)?;
    let dir = app.path().app_data_dir().map_err(e)?;
    std::fs::copy(&path, dir.join("restore.tmp")).map_err(e)?;
    let mut mem = Connection::open_in_memory().map_err(e)?;
    std::mem::swap(&mut *guard, &mut mem); drop(mem);
    std::fs::rename(dir.join("restore.tmp"), dir.join("prospects.db")).map_err(e)?;
    *guard = db::open(&dir.join("prospects.db")).map_err(e)?;
    Ok(())
}

/// Live search; results are returned unsaved so the rep chooses what to keep.
#[tauri::command] async fn search_companies(request: osm::SearchRequest) -> R<Vec<Prospect>> { osm::search(request).await }

/// Only reports success after a real request succeeds. Providers without an adapter report "Not implemented".
#[tauri::command] async fn test_connection(provider: String) -> R<String> {
    match provider.as_str() { "osm" => osm::test_connection().await, p => Err(format!("{p}: adapter not implemented; credential saved only")) }
}
#[tauri::command] fn set_secret(name: String, value: String) -> R<()> { secrets::set(&name, &value) }
#[tauri::command] fn delete_secret(name: String) -> R<()> { secrets::delete(&name) }
#[tauri::command] fn secret_status() -> Vec<(String, bool)> { secrets::KNOWN.iter().map(|k| (k.to_string(), secrets::has(k))).collect() }

#[tauri::command] fn get_setting(s: State<Db>, key: String) -> R<Option<String>> { repo::get_setting(&*lock(&s)?, &key).map_err(e) }
#[tauri::command] fn set_setting(s: State<Db>, key: String, value: String) -> R<()> { repo::set_setting(&*lock(&s)?, &key, &value).map_err(e) }

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let dir = app.path().app_data_dir()?;
            std::fs::create_dir_all(&dir)?;
            app.manage(Db(Mutex::new(db::open(&dir.join("prospects.db"))?)));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            list_regions, set_region_enabled, add_county, remove_county, list_prospects, get_prospect, save_prospect,
            delete_prospect, delete_all_data, set_stage, set_next_action, list_activities, add_activity, list_contacts, add_contact,
            list_signals, add_signal, field_sources, conflicts, dashboard, get_icp, save_icp, naics_catalog, lists, create_list, add_to_list, delete_list,
            save_search, saved_searches, delete_saved_search, add_task, list_tasks, complete_task, import_csv, export_csv, backup_db, restore_db,
            search_companies, test_connection, set_secret, delete_secret, secret_status, get_setting, set_setting
        ])
        .run(tauri::generate_context!())
        .expect("error running application");
}
