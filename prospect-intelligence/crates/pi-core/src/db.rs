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
