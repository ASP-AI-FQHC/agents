use crate::{dedupe, icp::{self, Icp}, models::*, naics, Error, Result};
use rusqlite::{params, Connection, Row};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

fn map_prospect(r: &Row) -> rusqlite::Result<Prospect> {
    let reasons: String = r.get("score_reasons")?;
    Ok(Prospect {
        id: r.get("id")?, name: r.get("name")?, website: r.get("website")?, phone: r.get("phone")?, address: r.get("address")?,
        city: r.get("city")?, county: r.get("county")?, state: r.get("state")?, zip: r.get("zip")?, lat: r.get("lat")?, lon: r.get("lon")?,
        vertical: r.get("vertical")?, description: r.get("description")?, employees_est: r.get("employees_est")?,
        employees_source: r.get("employees_source")?, source: r.get("source")?, source_id: r.get("source_id")?, source_url: r.get("source_url")?,
        stage: r.get("stage")?, score: r.get("score")?, score_reasons: serde_json::from_str(&reasons).unwrap_or_default(),
        next_action: r.get("next_action")?, next_action_due: r.get("next_action_due")?, legal_name: r.get("legal_name")?, naics: r.get("naics")?,
        sic: r.get("sic")?, revenue_range: r.get("revenue_range")?, year_established: r.get("year_established")?, parent_company: r.get("parent_company")?,
        facilities: r.get("facilities")?, products: r.get("products")?, owner: r.get("owner")?, tags: r.get("tags")?, origin: r.get("origin")?,
        export_ok: r.get::<_, i64>("export_ok")? != 0, tier: r.get("tier")?, completeness: r.get("completeness")?, updated_at: r.get("updated_at")?,
    })
}

/// Fields whose provenance and conflicts are tracked.
fn tracked(p: &Prospect) -> Vec<(&'static str, Option<String>)> {
    vec![("website", p.website.clone()), ("phone", p.phone.clone()), ("address", p.address.clone()), ("city", p.city.clone()),
         ("county", p.county.clone()), ("zip", p.zip.clone()), ("naics", p.naics.clone()), ("sic", p.sic.clone()),
         ("employees", p.employees_est.map(|n| n.to_string())), ("revenue_range", p.revenue_range.clone()), ("legal_name", p.legal_name.clone()),
         ("facilities", p.facilities.map(|n| n.to_string())), ("year_established", p.year_established.map(|n| n.to_string()))]
}

pub fn record_source(c: &Connection, pid: i64, field: &str, value: &str, source: &str, source_ref: Option<&str>, status: &str, confidence: Option<&str>) -> Result<()> {
    if value.trim().is_empty() { return Ok(()); }
    let dup: i64 = c.query_row("SELECT COUNT(*) FROM field_sources WHERE prospect_id=? AND field=? AND value=? AND source=?", params![pid, field, value, source], |r| r.get(0))?;
    if dup == 0 {
        c.execute("INSERT INTO field_sources(prospect_id,field,value,source,source_ref,status,confidence) VALUES(?,?,?,?,?,?,?)",
            params![pid, field, value, source, source_ref, status, confidence])?;
    }
    Ok(())
}

pub fn load_icp(c: &Connection) -> Result<Icp> {
    Ok(get_setting(c, "icp")?.and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default())
}
pub fn save_icp(c: &Connection, i: &Icp) -> Result<()> {
    i.validate().map_err(Error::Invalid)?;
    set_setting(c, "icp", &serde_json::to_string(i)?)?;
    rescore_all(c)
}

/// Recompute score, tier, completeness and explanation for one prospect.
pub fn rescore(c: &Connection, id: i64) -> Result<()> {
    let p = get_prospect(c, id)?;
    let contacts = list_contacts(c, id)?;
    let sig: i64 = c.query_row("SELECT COUNT(*) FROM signals WHERE prospect_id=? AND source<>''", [id], |r| r.get(0))?;
    let s = icp::score(&load_icp(c)?, &icp::Inputs { p: &p, contacts: &contacts, signals: sig as usize });
    c.execute("UPDATE prospects SET score=?,tier=?,completeness=?,score_reasons=? WHERE id=?",
        params![s.total, s.tier, s.completeness, serde_json::to_string(&s.lines)?, id])?;
    Ok(())
}
pub fn rescore_all(c: &Connection) -> Result<()> {
    let ids: Vec<i64> = c.prepare("SELECT id FROM prospects")?.query_map([], |r| r.get(0))?.collect::<std::result::Result<_, _>>()?;
    for id in ids { rescore(c, id)?; }
    Ok(())
}

#[derive(Debug, Serialize)]
pub struct Ingested { pub id: i64, pub merged: bool }

/// Insert-or-merge. New records get provenance for every populated tracked field; matches are enriched
/// (blank fields filled) and disagreeing values are preserved in `field_sources` for review.
pub fn ingest(c: &Connection, mut p: Prospect) -> Result<Ingested> {
    if p.name.trim().is_empty() { return Err(Error::Invalid("name required".into())); }
    if !VERTICALS.contains(&p.vertical.as_str()) { p.vertical = "other".into(); }
    if p.origin.is_empty() { p.origin = "manual".into(); }
    let src_ref = p.source_url.clone().or(p.source_id.clone());
    let cand = dedupe::Candidate { name: &p.name, website: p.website.as_deref(), address: p.address.as_deref(), zip: p.zip.as_deref(),
        city: p.city.as_deref(), source: &p.source, source_id: p.source_id.as_deref() };
    if let Some(id) = dedupe::find(c, &cand)? {
        let existing = get_prospect(c, id)?;
        let old: BTreeMap<_, _> = tracked(&existing).into_iter().collect();
        for (f, v) in tracked(&p) {
            let Some(v) = v else { continue };
            match old.get(f).cloned().flatten() {
                None => { c.execute(&format!("UPDATE prospects SET {}=? WHERE id=?", col(f)), params![v, id])?; }
                _ => {} // existing value's provenance is already stored; a differing value is kept below and surfaces in conflicts()
            }
            record_source(c, id, f, &v, &p.source, src_ref.as_deref(), "unverified", None)?;
        }
        c.execute("UPDATE prospects SET updated_at=CURRENT_TIMESTAMP WHERE id=?", [id])?;
        rescore(c, id)?;
        return Ok(Ingested { id, merged: true });
    }
    c.execute(
        "INSERT INTO prospects(name,website,phone,address,city,county,state,zip,lat,lon,vertical,description,employees_est,employees_source,source,source_id,source_url,
         legal_name,naics,sic,revenue_range,year_established,parent_company,facilities,products,owner,tags,origin,export_ok)
         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        params![p.name,p.website,p.phone,p.address,p.city,p.county,p.state,p.zip,p.lat,p.lon,p.vertical,p.description,p.employees_est,p.employees_source,
            p.source,p.source_id,p.source_url,p.legal_name,p.naics,p.sic,p.revenue_range,p.year_established,p.parent_company,p.facilities,p.products,
            p.owner,p.tags,p.origin,p.export_ok as i64])?;
    let id = c.last_insert_rowid();
    for (f, v) in tracked(&p) { if let Some(v) = v { record_source(c, id, f, &v, &p.source, src_ref.as_deref(), "unverified", None)?; } }
    rescore(c, id)?;
    Ok(Ingested { id, merged: false })
}

fn col(field: &str) -> &'static str {
    match field { "employees" => "employees_est", "website" => "website", "phone" => "phone", "address" => "address", "city" => "city", "county" => "county",
        "zip" => "zip", "naics" => "naics", "sic" => "sic", "revenue_range" => "revenue_range", "legal_name" => "legal_name",
        "facilities" => "facilities", "year_established" => "year_established", _ => "name" }
}

/// Manual edit of an existing record (user-entered values are labelled source "manual").
pub fn update_prospect(c: &Connection, p: &Prospect) -> Result<()> {
    c.execute(
        "UPDATE prospects SET name=?,website=?,phone=?,address=?,city=?,county=?,state=?,zip=?,vertical=?,description=?,employees_est=?,employees_source=?,
         legal_name=?,naics=?,sic=?,revenue_range=?,year_established=?,parent_company=?,facilities=?,products=?,owner=?,tags=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
        params![p.name,p.website,p.phone,p.address,p.city,p.county,p.state,p.zip,p.vertical,p.description,p.employees_est,p.employees_source,
            p.legal_name,p.naics,p.sic,p.revenue_range,p.year_established,p.parent_company,p.facilities,p.products,p.owner,p.tags,p.id])?;
    for (f, v) in tracked(p) { if let Some(v) = v { record_source(c, p.id, f, &v, "manual", None, "verified", None)?; } }
    rescore(c, p.id)
}

#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Filter { pub text: Option<String>, pub stage: Option<String>, pub state: Option<String>, pub county: Option<String>,
    pub vertical: Option<String>, pub min_score: Option<i64>, pub naics: Option<Vec<String>>, pub tier: Option<String>, pub origin: Option<String>,
    pub list_id: Option<i64>, pub min_employees: Option<i64>, pub max_employees: Option<i64> }

pub fn list_prospects(c: &Connection, f: &Filter) -> Result<Vec<Prospect>> {
    let mut st = c.prepare(
        "SELECT * FROM prospects WHERE (?1 IS NULL OR name LIKE '%'||?1||'%' OR city LIKE '%'||?1||'%' OR tags LIKE '%'||?1||'%')
         AND (?2 IS NULL OR stage=?2) AND (?3 IS NULL OR state=?3) AND (?4 IS NULL OR county=?4) AND (?5 IS NULL OR vertical=?5)
         AND score>=COALESCE(?6,0) AND (?7 IS NULL OR tier=?7) AND (?8 IS NULL OR origin=?8)
         AND (?9 IS NULL OR id IN (SELECT prospect_id FROM list_members WHERE list_id=?9))
         AND (?10 IS NULL OR employees_est>=?10) AND (?11 IS NULL OR employees_est<=?11)
         ORDER BY score DESC, name LIMIT 5000")?;
    let rows = st.query_map(params![f.text, f.stage, f.state, f.county, f.vertical, f.min_score, f.tier, f.origin, f.list_id, f.min_employees, f.max_employees], map_prospect)?;
    let all: Vec<Prospect> = rows.collect::<std::result::Result<_, _>>()?;
    Ok(match &f.naics { Some(sel) if !sel.is_empty() => all.into_iter().filter(|p| naics::matches(p.naics.as_deref(), sel)).collect(), _ => all })
}

pub fn get_prospect(c: &Connection, id: i64) -> Result<Prospect> { Ok(c.query_row("SELECT * FROM prospects WHERE id=?", [id], map_prospect)?) }

pub fn set_stage(c: &Connection, id: i64, stage: &str) -> Result<()> {
    if !STAGES.contains(&stage) { return Err(Error::Invalid(format!("bad stage {stage}"))); }
    c.execute("UPDATE prospects SET stage=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", params![stage, id])?;
    add_activity(c, id, "stage", &format!("Stage changed to {stage}"))?;
    Ok(())
}
pub fn set_next_action(c: &Connection, id: i64, action: Option<&str>, due: Option<&str>) -> Result<()> {
    c.execute("UPDATE prospects SET next_action=?,next_action_due=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", params![action, due, id])?; Ok(())
}
pub fn delete_prospect(c: &Connection, id: i64) -> Result<()> { c.execute("DELETE FROM prospects WHERE id=?", [id])?; Ok(()) }
/// User-controlled deletion of everything: records, lists, searches. Settings kept.
pub fn delete_all_data(c: &Connection) -> Result<()> {
    c.execute_batch("DELETE FROM prospects; DELETE FROM lists; DELETE FROM saved_searches;")?; Ok(())
}

pub fn add_activity(c: &Connection, pid: i64, kind: &str, summary: &str) -> Result<i64> {
    c.execute("INSERT INTO activities(prospect_id,kind,summary) VALUES(?,?,?)", params![pid, kind, summary])?; Ok(c.last_insert_rowid())
}
pub fn list_activities(c: &Connection, pid: i64) -> Result<Vec<Activity>> {
    let mut st = c.prepare("SELECT id,prospect_id,kind,summary,occurred_at FROM activities WHERE prospect_id=? ORDER BY occurred_at DESC,id DESC")?;
    let rows = st.query_map([pid], |r| Ok(Activity { id: r.get(0)?, prospect_id: r.get(1)?, kind: r.get(2)?, summary: r.get(3)?, occurred_at: r.get(4)? }))?;
    Ok(rows.collect::<std::result::Result<_, _>>()?)
}

pub fn add_contact(c: &Connection, k: &Contact) -> Result<i64> {
    let verified_on = if k.verified { Some(k.verified_on.clone().unwrap_or_else(|| chrono::Utc::now().format("%Y-%m-%d").to_string())) } else { None };
    c.execute("INSERT INTO contacts(prospect_id,name,title,email,phone,linkedin,source,verified,profile_url,verified_on) VALUES(?,?,?,?,?,?,?,?,?,?)",
        params![k.prospect_id,k.name,k.title,k.email,k.phone,k.linkedin,k.source,k.verified as i64,k.profile_url,verified_on])?;
    let id = c.last_insert_rowid(); rescore(c, k.prospect_id)?; Ok(id)
}
pub fn list_contacts(c: &Connection, pid: i64) -> Result<Vec<Contact>> {
    let mut st = c.prepare("SELECT id,prospect_id,name,title,email,phone,linkedin,source,verified,profile_url,verified_on FROM contacts WHERE prospect_id=?")?;
    let rows = st.query_map([pid], |r| Ok(Contact { id: r.get(0)?, prospect_id: r.get(1)?, name: r.get(2)?, title: r.get(3)?, email: r.get(4)?, phone: r.get(5)?,
        linkedin: r.get(6)?, source: r.get(7)?, verified: r.get::<_, i64>(8)? != 0, profile_url: r.get(9)?, verified_on: r.get(10)? }))?;
    Ok(rows.collect::<std::result::Result<_, _>>()?)
}

pub fn add_signal(c: &Connection, s: &Signal) -> Result<i64> {
    if s.source.trim().is_empty() { return Err(Error::Invalid("a signal requires a source".into())); }
    c.execute("INSERT INTO signals(prospect_id,kind,summary,source,source_url,event_date) VALUES(?,?,?,?,?,?)", params![s.prospect_id,s.kind,s.summary,s.source,s.source_url,s.event_date])?;
    let id = c.last_insert_rowid(); rescore(c, s.prospect_id)?; Ok(id)
}
pub fn list_signals(c: &Connection, pid: i64) -> Result<Vec<Signal>> {
    let mut st = c.prepare("SELECT id,prospect_id,kind,summary,source,source_url,event_date FROM signals WHERE prospect_id=? ORDER BY event_date DESC")?;
    let rows = st.query_map([pid], |r| Ok(Signal { id: r.get(0)?, prospect_id: r.get(1)?, kind: r.get(2)?, summary: r.get(3)?, source: r.get(4)?, source_url: r.get(5)?, event_date: r.get(6)? }))?;
    Ok(rows.collect::<std::result::Result<_, _>>()?)
}

pub fn field_sources(c: &Connection, pid: i64) -> Result<Vec<FieldSource>> {
    let mut st = c.prepare("SELECT id,prospect_id,field,value,source,source_ref,retrieved_at,status,confidence FROM field_sources WHERE prospect_id=? ORDER BY field,retrieved_at")?;
    let rows = st.query_map([pid], |r| Ok(FieldSource { id: r.get(0)?, prospect_id: r.get(1)?, field: r.get(2)?, value: r.get(3)?, source: r.get(4)?, source_ref: r.get(5)?,
        retrieved_at: r.get(6)?, status: r.get(7)?, confidence: r.get(8)? }))?;
    Ok(rows.collect::<std::result::Result<_, _>>()?)
}
fn norm_val(field: &str, v: &str) -> String {
    match field {
        "phone" => { let d: String = v.chars().filter(|c| c.is_ascii_digit()).collect(); d.strip_prefix('1').filter(|r| r.len() == 10).map(str::to_string).unwrap_or(d) }
        "website" => dedupe::domain(v).unwrap_or_else(|| v.trim().to_lowercase()),
        "address" => dedupe::norm_addr(v),
        _ => v.trim().to_lowercase(),
    }
}
/// Fields where sources disagree, for user review.
pub fn conflicts(c: &Connection, pid: i64) -> Result<Vec<Conflict>> {
    let mut by: BTreeMap<String, Vec<FieldSource>> = BTreeMap::new();
    for s in field_sources(c, pid)? { by.entry(s.field.clone()).or_default().push(s); }
    Ok(by.into_iter().filter(|(_, v)| { let mut d: Vec<_> = v.iter().map(|x| norm_val(&x.field, &x.value)).collect(); d.sort(); d.dedup(); d.len() > 1 })
        .map(|(field, values)| Conflict { field, values }).collect())
}

// ---- lists, tasks, saved searches ----
pub fn create_list(c: &Connection, name: &str) -> Result<i64> {
    c.execute("INSERT INTO lists(name) VALUES(?) ON CONFLICT(name) DO NOTHING", [name.trim()])?;
    Ok(c.query_row("SELECT id FROM lists WHERE name=?", [name.trim()], |r| r.get(0))?)
}
pub fn lists(c: &Connection) -> Result<Vec<(i64, String, i64)>> {
    let mut st = c.prepare("SELECT l.id,l.name,(SELECT COUNT(*) FROM list_members m WHERE m.list_id=l.id) FROM lists l ORDER BY l.name")?;
    let rows = st.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?; Ok(rows.collect::<std::result::Result<_, _>>()?)
}
pub fn add_to_list(c: &Connection, list: i64, ids: &[i64]) -> Result<()> {
    for id in ids { c.execute("INSERT OR IGNORE INTO list_members VALUES(?,?)", params![list, id])?; } Ok(())
}
pub fn delete_list(c: &Connection, list: i64) -> Result<()> { c.execute("DELETE FROM lists WHERE id=?", [list])?; Ok(()) }
pub fn save_search(c: &Connection, name: &str, kind: &str, body: &str) -> Result<i64> {
    c.execute("INSERT INTO saved_searches(name,kind,body) VALUES(?,?,?)", params![name, kind, body])?; Ok(c.last_insert_rowid())
}
pub fn saved_searches(c: &Connection, kind: &str) -> Result<Vec<(i64, String, String)>> {
    let mut st = c.prepare("SELECT id,name,body FROM saved_searches WHERE kind=? ORDER BY name")?;
    let rows = st.query_map([kind], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?; Ok(rows.collect::<std::result::Result<_, _>>()?)
}
pub fn delete_saved_search(c: &Connection, id: i64) -> Result<()> { c.execute("DELETE FROM saved_searches WHERE id=?", [id])?; Ok(()) }
pub fn add_task(c: &Connection, pid: i64, title: &str, due: Option<&str>) -> Result<i64> {
    c.execute("INSERT INTO tasks(prospect_id,title,due) VALUES(?,?,?)", params![pid, title, due])?; Ok(c.last_insert_rowid())
}
pub fn list_tasks(c: &Connection, pid: i64) -> Result<Vec<(i64, String, Option<String>, bool)>> {
    let mut st = c.prepare("SELECT id,title,due,done FROM tasks WHERE prospect_id=? ORDER BY done,due")?;
    let rows = st.query_map([pid], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get::<_, i64>(3)? != 0)))?; Ok(rows.collect::<std::result::Result<_, _>>()?)
}
pub fn complete_task(c: &Connection, id: i64, done: bool) -> Result<()> { c.execute("UPDATE tasks SET done=? WHERE id=?", params![done as i64, id])?; Ok(()) }

// ---- dashboard ----
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Dashboard { pub total: i64, pub new: i64, pub priority_a: i64, pub qualified: i64, pub contacted: i64, pub meetings: i64, pub opportunities: i64,
    pub by_state: Vec<(String, i64)>, pub by_county: Vec<(String, i64)>, pub by_industry: Vec<(String, i64)>, pub by_size: Vec<(String, i64)>,
    pub by_tier: Vec<(String, i64)>, pub by_stage: Vec<(String, i64)> }

fn group(c: &Connection, sql: &str) -> Result<Vec<(String, i64)>> {
    let mut st = c.prepare(sql)?; let rows = st.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?; Ok(rows.collect::<std::result::Result<_, _>>()?)
}
pub fn dashboard(c: &Connection) -> Result<Dashboard> {
    let n = |w: &str| -> Result<i64> { Ok(c.query_row(&format!("SELECT COUNT(*) FROM prospects {w}"), [], |r| r.get(0))?) };
    Ok(Dashboard { total: n("")?, new: n("WHERE stage='new'")?, priority_a: n("WHERE tier='A'")?, qualified: n("WHERE stage='qualified'")?,
        contacted: n("WHERE stage='contacted'")?, meetings: n("WHERE stage='meeting'")?, opportunities: n("WHERE stage IN ('opportunity','proposal')")?,
        by_state: group(c, "SELECT COALESCE(state,'Unknown'),COUNT(*) FROM prospects GROUP BY 1 ORDER BY 2 DESC")?,
        by_county: group(c, "SELECT COALESCE(county,'Unknown'),COUNT(*) FROM prospects GROUP BY 1 ORDER BY 2 DESC LIMIT 15")?,
        by_industry: group(c, "SELECT vertical,COUNT(*) FROM prospects GROUP BY 1 ORDER BY 2 DESC")?,
        by_size: group(c, "SELECT CASE WHEN employees_est IS NULL THEN 'Not available' WHEN employees_est<10 THEN '1–9' WHEN employees_est<50 THEN '10–49' WHEN employees_est<250 THEN '50–249' WHEN employees_est<=500 THEN '250–500' ELSE '500+' END,COUNT(*) FROM prospects GROUP BY 1")?,
        by_tier: group(c, "SELECT tier,COUNT(*) FROM prospects GROUP BY 1 ORDER BY 1")?,
        by_stage: group(c, "SELECT stage,COUNT(*) FROM prospects GROUP BY 1")? })
}

pub fn get_setting(c: &Connection, k: &str) -> Result<Option<String>> {
    match c.query_row("SELECT value FROM settings WHERE key=?", [k], |r| r.get(0)) {
        Ok(v) => Ok(Some(v)), Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None), Err(e) => Err(e.into()) }
}
pub fn set_setting(c: &Connection, k: &str, v: &str) -> Result<()> {
    c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", params![k, v])?; Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db;
    fn sample() -> Prospect { Prospect { name: "Acme Fabrication, Inc.".into(), vertical: "manufacturing".into(), state: Some("MO".into()), county: Some("St. Louis".into()),
        city: Some("Maryland Heights".into()), zip: Some("63043".into()), website: Some("https://www.acmefab.com".into()), source: "osm".into(),
        source_id: Some("node/1".into()), origin: "live".into(), ..Default::default() } }

    #[test]
    fn persistence_across_reopen() {
        let dir = std::env::temp_dir().join(format!("pi-test-{}.db", std::process::id()));
        let _ = std::fs::remove_file(&dir);
        let id = { let c = db::open(&dir).unwrap(); let i = ingest(&c, sample()).unwrap(); add_activity(&c, i.id, "note", "hello").unwrap(); i.id };
        let c = db::open(&dir).unwrap();
        assert_eq!(get_prospect(&c, id).unwrap().name, "Acme Fabrication, Inc.");
        assert_eq!(list_activities(&c, id).unwrap()[0].summary, "hello");
        drop(c); let _ = std::fs::remove_file(&dir);
    }
    #[test]
    fn dedupe_merges_and_preserves_conflicts() {
        let c = db::open_memory().unwrap();
        let a = ingest(&c, sample()).unwrap();
        let mut other = Prospect { name: "ACME Fabrication Inc".into(), state: Some("MO".into()), zip: Some("63043".into()), source: "csv:leads.csv".into(),
            origin: "imported".into(), phone: Some("314-555-0100".into()), employees_est: Some(80), employees_source: Some("csv".into()), ..Default::default() };
        other.website = Some("acmefab.com/contact".into());
        let b = ingest(&c, other).unwrap();
        assert!(b.merged && a.id == b.id);
        let p = get_prospect(&c, a.id).unwrap();
        assert_eq!(p.phone.as_deref(), Some("314-555-0100")); assert_eq!(p.employees_est, Some(80));
        let mut conflicting = sample(); conflicting.source = "csv:b.csv".into(); conflicting.source_id = None; conflicting.phone = Some("314-555-0199".into());
        ingest(&c, conflicting).unwrap();
        let cf = conflicts(&c, a.id).unwrap();
        assert!(cf.iter().any(|x| x.field == "phone" && x.values.len() == 2), "{cf:?}");
        assert_eq!(list_prospects(&c, &Filter::default()).unwrap().len(), 1);
    }
    #[test]
    fn filters_naics_county_size() {
        let c = db::open_memory().unwrap();
        let mut a = sample(); a.naics = Some("332710".into()); a.employees_est = Some(60); a.employees_source = Some("csv".into());
        let mut b = sample(); b.name = "Beta Foods".into(); b.source_id = Some("node/2".into()); b.website = None; b.state = Some("IL".into());
        b.county = Some("St. Clair".into()); b.naics = Some("311999".into()); b.employees_est = Some(400); b.employees_source = Some("csv".into());
        b.zip = Some("62201".into());
        ingest(&c, a).unwrap(); ingest(&c, b).unwrap();
        let f = |x: Filter| list_prospects(&c, &x).unwrap().len();
        assert_eq!(f(Filter { naics: Some(vec!["metal-fab".into()]), ..Default::default() }), 1);
        assert_eq!(f(Filter { naics: Some(vec!["sector-mfg".into()]), ..Default::default() }), 2);
        assert_eq!(f(Filter { state: Some("IL".into()), county: Some("St. Clair".into()), ..Default::default() }), 1);
        assert_eq!(f(Filter { min_employees: Some(100), ..Default::default() }), 1);
    }
    #[test]
    fn scoring_persists_and_contact_raises_it() {
        let c = db::open_memory().unwrap();
        let id = ingest(&c, sample()).unwrap().id;
        let before = get_prospect(&c, id).unwrap();
        assert!(!before.score_reasons.is_empty());
        add_contact(&c, &Contact { id: 0, prospect_id: id, name: "Pat Lee".into(), title: Some("President".into()), email: Some("pat@acmefab.com".into()), phone: None,
            linkedin: None, source: "manual".into(), verified: true, profile_url: None, verified_on: None }).unwrap();
        assert!(get_prospect(&c, id).unwrap().score > before.score);
        assert!(add_signal(&c, &Signal { id: 0, prospect_id: id, kind: "expansion".into(), summary: "x".into(), source: "".into(), source_url: None, event_date: None }).is_err());
    }
    #[test]
    fn pipeline_lists_tasks_dashboard() {
        let c = db::open_memory().unwrap();
        let id = ingest(&c, sample()).unwrap().id;
        set_stage(&c, id, "qualified").unwrap(); assert!(set_stage(&c, id, "bogus").is_err());
        let l = create_list(&c, "Q1 targets").unwrap(); add_to_list(&c, l, &[id]).unwrap();
        assert_eq!(list_prospects(&c, &Filter { list_id: Some(l), ..Default::default() }).unwrap().len(), 1);
        let t = add_task(&c, id, "Call", Some("2026-12-01")).unwrap(); complete_task(&c, t, true).unwrap();
        assert!(list_tasks(&c, id).unwrap()[0].3);
        let d = dashboard(&c).unwrap(); assert_eq!((d.total, d.qualified), (1, 1));
        delete_all_data(&c).unwrap(); assert_eq!(dashboard(&c).unwrap().total, 0);
    }
    #[test]
    fn icp_save_rescores() {
        let c = db::open_memory().unwrap();
        let id = ingest(&c, sample()).unwrap().id;
        let s0 = get_prospect(&c, id).unwrap().score;
        let mut i = Icp::default(); i.weights.industry = 30; i.weights.complexity = 30; i.weights.contact = 0; i.weights.geo = 0; save_icp(&c, &i).unwrap();
        assert_ne!(get_prospect(&c, id).unwrap().score, s0);
        i.weights.industry = 99; assert!(save_icp(&c, &i).is_err());
    }
}
