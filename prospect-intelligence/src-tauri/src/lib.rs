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
#[tauri::command] fn save_prospect(s: State<Db>, mut prospect: Prospect) -> R<i64> {
    if prospect.source.is_empty() { prospect.source = "manual".into(); }
    repo::upsert_prospect(&*lock(&s)?, prospect).map_err(e)
}
#[tauri::command] fn delete_prospect(s: State<Db>, id: i64) -> R<()> { repo::delete_prospect(&*lock(&s)?, id).map_err(e) }
#[tauri::command] fn set_stage(s: State<Db>, id: i64, stage: String) -> R<()> { repo::set_stage(&*lock(&s)?, id, &stage).map_err(e) }
#[tauri::command] fn set_next_action(s: State<Db>, id: i64, action: Option<String>, due: Option<String>) -> R<()> {
    repo::set_next_action(&*lock(&s)?, id, action.as_deref(), due.as_deref()).map_err(e) }
#[tauri::command] fn list_activities(s: State<Db>, id: i64) -> R<Vec<Activity>> { repo::list_activities(&*lock(&s)?, id).map_err(e) }
#[tauri::command] fn add_activity(s: State<Db>, id: i64, kind: String, summary: String) -> R<i64> { repo::add_activity(&*lock(&s)?, id, &kind, &summary).map_err(e) }
#[tauri::command] fn list_contacts(s: State<Db>, id: i64) -> R<Vec<Contact>> { repo::list_contacts(&*lock(&s)?, id).map_err(e) }
#[tauri::command] fn add_contact(s: State<Db>, contact: Contact) -> R<i64> { repo::add_contact(&*lock(&s)?, &contact).map_err(e) }

/// Live search; results are returned unsaved so the rep chooses what to keep.
#[tauri::command] async fn search_companies(request: osm::SearchRequest) -> R<Vec<Prospect>> { osm::search(request).await }

#[tauri::command] fn set_secret(name: String, value: String) -> R<()> { secrets::set(&name, &value) }
#[tauri::command] fn delete_secret(name: String) -> R<()> { secrets::delete(&name) }
#[tauri::command] fn secret_status() -> Vec<(String, bool)> { secrets::KNOWN.iter().map(|k| (k.to_string(), secrets::has(k))).collect() }

#[tauri::command] fn get_setting(s: State<Db>, key: String) -> R<Option<String>> { repo::get_setting(&*lock(&s)?, &key).map_err(e) }
#[tauri::command] fn set_setting(s: State<Db>, key: String, value: String) -> R<()> { repo::set_setting(&*lock(&s)?, &key, &value).map_err(e) }

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let dir = app.path().app_data_dir()?;
            std::fs::create_dir_all(&dir)?;
            app.manage(Db(Mutex::new(db::open(&dir.join("prospects.db"))?)));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            list_regions, set_region_enabled, add_county, remove_county, list_prospects, get_prospect, save_prospect,
            delete_prospect, set_stage, set_next_action, list_activities, add_activity, list_contacts, add_contact,
            search_companies, set_secret, delete_secret, secret_status, get_setting, set_setting
        ])
        .run(tauri::generate_context!())
        .expect("error running application");
}
