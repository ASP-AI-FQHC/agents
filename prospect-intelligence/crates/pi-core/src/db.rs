use crate::{geo, Result};
use rusqlite::Connection;
use std::path::Path;

const MIGRATIONS: &[&str] = &[r#"
CREATE TABLE regions(
  id INTEGER PRIMARY KEY, state TEXT NOT NULL, name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'county', enabled INTEGER NOT NULL DEFAULT 1,
  is_custom INTEGER NOT NULL DEFAULT 0, UNIQUE(state, name, kind));
CREATE TABLE prospects(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, website TEXT, phone TEXT,
  address TEXT, city TEXT, county TEXT, state TEXT, zip TEXT,
  lat REAL, lon REAL, vertical TEXT NOT NULL DEFAULT 'other',
  description TEXT, employees_est INTEGER, employees_source TEXT,
  source TEXT NOT NULL, source_id TEXT, source_url TEXT,
  stage TEXT NOT NULL DEFAULT 'new', score INTEGER NOT NULL DEFAULT 0,
  score_reasons TEXT NOT NULL DEFAULT '[]', next_action TEXT, next_action_due TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source, source_id));
CREATE INDEX idx_prospects_stage ON prospects(stage);
CREATE INDEX idx_prospects_county ON prospects(state, county);
CREATE TABLE contacts(
  id INTEGER PRIMARY KEY, prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
  name TEXT NOT NULL, title TEXT, email TEXT, phone TEXT, linkedin TEXT,
  source TEXT NOT NULL DEFAULT 'manual', verified INTEGER NOT NULL DEFAULT 0);
CREATE TABLE activities(
  id INTEGER PRIMARY KEY, prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, summary TEXT NOT NULL, occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"#, r#"
ALTER TABLE prospects ADD COLUMN legal_name TEXT;
ALTER TABLE prospects ADD COLUMN naics TEXT;
ALTER TABLE prospects ADD COLUMN sic TEXT;
ALTER TABLE prospects ADD COLUMN revenue_range TEXT;
ALTER TABLE prospects ADD COLUMN year_established INTEGER;
ALTER TABLE prospects ADD COLUMN parent_company TEXT;
ALTER TABLE prospects ADD COLUMN facilities INTEGER;
ALTER TABLE prospects ADD COLUMN products TEXT;
ALTER TABLE prospects ADD COLUMN owner TEXT;
ALTER TABLE prospects ADD COLUMN tags TEXT;
ALTER TABLE prospects ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE prospects ADD COLUMN export_ok INTEGER NOT NULL DEFAULT 1;
ALTER TABLE prospects ADD COLUMN tier TEXT NOT NULL DEFAULT 'Low';
ALTER TABLE prospects ADD COLUMN completeness INTEGER NOT NULL DEFAULT 0;
ALTER TABLE contacts ADD COLUMN profile_url TEXT;
ALTER TABLE contacts ADD COLUMN verified_on TEXT;
CREATE TABLE field_sources(
  id INTEGER PRIMARY KEY, prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
  field TEXT NOT NULL, value TEXT NOT NULL, source TEXT NOT NULL, source_ref TEXT,
  retrieved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, status TEXT NOT NULL DEFAULT 'unverified', confidence TEXT);
CREATE INDEX idx_fs_prospect ON field_sources(prospect_id, field);
CREATE TABLE signals(
  id INTEGER PRIMARY KEY, prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, summary TEXT NOT NULL, source TEXT NOT NULL, source_url TEXT, event_date TEXT);
CREATE TABLE lists(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE list_members(list_id INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
  prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE, PRIMARY KEY(list_id, prospect_id));
CREATE TABLE tasks(id INTEGER PRIMARY KEY, prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
  title TEXT NOT NULL, due TEXT, done INTEGER NOT NULL DEFAULT 0);
CREATE TABLE saved_searches(id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL);
"#];

pub fn open(path: &Path) -> Result<Connection> {
    let mut c = Connection::open(path)?;
    init(&mut c)?;
    Ok(c)
}

pub fn open_memory() -> Result<Connection> {
    let mut c = Connection::open_in_memory()?;
    init(&mut c)?;
    Ok(c)
}

fn init(c: &mut Connection) -> Result<()> {
    c.execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;")?;
    let ver: usize = c.query_row("PRAGMA user_version", [], |r| r.get(0))?;
    for (i, sql) in MIGRATIONS.iter().enumerate().skip(ver) {
        let tx = c.transaction()?;
        tx.execute_batch(sql)?;
        if i == 0 {
            geo::seed(&tx)?;
        }
        tx.execute_batch(&format!("PRAGMA user_version={}", i + 1))?;
        tx.commit()?;
    }
    Ok(())
}
