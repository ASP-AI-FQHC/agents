use crate::{models::*, scoring, Error, Result};
use rusqlite::{params, Connection, Row};

fn map_prospect(r: &Row) -> rusqlite::Result<Prospect> {
    let reasons: String = r.get("score_reasons")?;
    Ok(Prospect {
        id: r.get("id")?, name: r.get("name")?, website: r.get("website")?, phone: r.get("phone")?,
        address: r.get("address")?, city: r.get("city")?, county: r.get("county")?, state: r.get("state")?,
        zip: r.get("zip")?, lat: r.get("lat")?, lon: r.get("lon")?, vertical: r.get("vertical")?,
        description: r.get("description")?, employees_est: r.get("employees_est")?,
        employees_source: r.get("employees_source")?, source: r.get("source")?, source_id: r.get("source_id")?,
        source_url: r.get("source_url")?, stage: r.get("stage")?, score: r.get("score")?,
        score_reasons: serde_json::from_str(&reasons).unwrap_or_default(),
        next_action: r.get("next_action")?, next_action_due: r.get("next_action_due")?,
    })
}

/// Insert or update by (source, source_id); manual records (no source_id) always insert. Re-scores.
pub fn upsert_prospect(c: &Connection, mut p: Prospect) -> Result<i64> {
    if p.name.trim().is_empty() { return Err(Error::Invalid("name required".into())); }
    if !VERTICALS.contains(&p.vertical.as_str()) { p.vertical = "other".into(); }
    let (s, why) = scoring::score(&p);
    let why = serde_json::to_string(&why)?;
    if p.id > 0 {
        c.execute(
            "UPDATE prospects SET name=?,website=?,phone=?,address=?,city=?,county=?,state=?,zip=?,vertical=?,description=?,
             employees_est=?,employees_source=?,score=?,score_reasons=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            params![p.name,p.website,p.phone,p.address,p.city,p.county,p.state,p.zip,p.vertical,p.description,
                    p.employees_est,p.employees_source,s,why,p.id])?;
        return Ok(p.id);
    }
    c.execute(
        "INSERT INTO prospects(name,website,phone,address,city,county,state,zip,lat,lon,vertical,description,employees_est,
         employees_source,source,source_id,source_url,score,score_reasons) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
         ON CONFLICT(source,source_id) DO UPDATE SET name=excluded.name, website=excluded.website, phone=excluded.phone,
         address=excluded.address, city=excluded.city, county=excluded.county, zip=excluded.zip, lat=excluded.lat,
         lon=excluded.lon, updated_at=CURRENT_TIMESTAMP",
        params![p.name,p.website,p.phone,p.address,p.city,p.county,p.state,p.zip,p.lat,p.lon,p.vertical,p.description,
                p.employees_est,p.employees_source,p.source,p.source_id,p.source_url,s,why])?;
    if p.source_id.is_some() {
        c.query_row("SELECT id FROM prospects WHERE source=? AND source_id=?", params![p.source, p.source_id], |r| r.get(0))
            .map_err(Into::into)
    } else { Ok(c.last_insert_rowid()) }
}

#[derive(Debug, Default, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Filter { pub text: Option<String>, pub stage: Option<String>, pub state: Option<String>,
    pub county: Option<String>, pub vertical: Option<String>, pub min_score: Option<i64> }

pub fn list_prospects(c: &Connection, f: &Filter) -> Result<Vec<Prospect>> {
    let mut st = c.prepare(
        "SELECT * FROM prospects WHERE (?1 IS NULL OR name LIKE '%'||?1||'%' OR city LIKE '%'||?1||'%')
         AND (?2 IS NULL OR stage=?2) AND (?3 IS NULL OR state=?3) AND (?4 IS NULL OR county=?4)
         AND (?5 IS NULL OR vertical=?5) AND score>=COALESCE(?6,0) ORDER BY score DESC, name LIMIT 1000")?;
    let rows = st.query_map(params![f.text, f.stage, f.state, f.county, f.vertical, f.min_score], map_prospect)?;
    Ok(rows.collect::<std::result::Result<_, _>>()?)
}

pub fn get_prospect(c: &Connection, id: i64) -> Result<Prospect> {
    Ok(c.query_row("SELECT * FROM prospects WHERE id=?", [id], map_prospect)?)
}

pub fn set_stage(c: &Connection, id: i64, stage: &str) -> Result<()> {
    if !STAGES.contains(&stage) { return Err(Error::Invalid(format!("bad stage {stage}"))); }
    c.execute("UPDATE prospects SET stage=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", params![stage, id])?;
    add_activity(c, id, "stage", &format!("Stage changed to {stage}"))?;
    Ok(())
}

pub fn set_next_action(c: &Connection, id: i64, action: Option<&str>, due: Option<&str>) -> Result<()> {
    c.execute("UPDATE prospects SET next_action=?,next_action_due=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", params![action, due, id])?;
    Ok(())
}

pub fn delete_prospect(c: &Connection, id: i64) -> Result<()> { c.execute("DELETE FROM prospects WHERE id=?", [id])?; Ok(()) }

pub fn add_activity(c: &Connection, pid: i64, kind: &str, summary: &str) -> Result<i64> {
    c.execute("INSERT INTO activities(prospect_id,kind,summary) VALUES(?,?,?)", params![pid, kind, summary])?;
    Ok(c.last_insert_rowid())
}
pub fn list_activities(c: &Connection, pid: i64) -> Result<Vec<Activity>> {
    let mut st = c.prepare("SELECT id,prospect_id,kind,summary,occurred_at FROM activities WHERE prospect_id=? ORDER BY occurred_at DESC,id DESC")?;
    let rows = st.query_map([pid], |r| Ok(Activity { id: r.get(0)?, prospect_id: r.get(1)?, kind: r.get(2)?, summary: r.get(3)?, occurred_at: r.get(4)? }))?;
    Ok(rows.collect::<std::result::Result<_, _>>()?)
}

pub fn add_contact(c: &Connection, k: &Contact) -> Result<i64> {
    c.execute("INSERT INTO contacts(prospect_id,name,title,email,phone,linkedin,source,verified) VALUES(?,?,?,?,?,?,?,?)",
        params![k.prospect_id,k.name,k.title,k.email,k.phone,k.linkedin,k.source,k.verified as i64])?;
    Ok(c.last_insert_rowid())
}
pub fn list_contacts(c: &Connection, pid: i64) -> Result<Vec<Contact>> {
    let mut st = c.prepare("SELECT id,prospect_id,name,title,email,phone,linkedin,source,verified FROM contacts WHERE prospect_id=?")?;
    let rows = st.query_map([pid], |r| Ok(Contact { id: r.get(0)?, prospect_id: r.get(1)?, name: r.get(2)?, title: r.get(3)?,
        email: r.get(4)?, phone: r.get(5)?, linkedin: r.get(6)?, source: r.get(7)?, verified: r.get::<_, i64>(8)? != 0 }))?;
    Ok(rows.collect::<std::result::Result<_, _>>()?)
}

pub fn get_setting(c: &Connection, k: &str) -> Result<Option<String>> {
    match c.query_row("SELECT value FROM settings WHERE key=?", [k], |r| r.get(0)) {
        Ok(v) => Ok(Some(v)), Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None), Err(e) => Err(e.into()) }
}
pub fn set_setting(c: &Connection, k: &str, v: &str) -> Result<()> {
    c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", params![k, v])?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db;
    fn sample() -> Prospect { Prospect { name: "Acme Fabrication".into(), vertical: "manufacturing".into(), state: Some("MO".into()),
        county: Some("St. Louis".into()), source: "osm".into(), source_id: Some("node/1".into()), employees_est: Some(60),
        employees_source: Some("manual".into()), ..Default::default() } }
    #[test]
    fn upsert_dedupes_and_scores() {
        let c = db::open_memory().unwrap();
        let a = upsert_prospect(&c, sample()).unwrap();
        let b = upsert_prospect(&c, sample()).unwrap();
        assert_eq!(a, b);
        let p = get_prospect(&c, a).unwrap();
        assert!(p.score > 0 && !p.score_reasons.is_empty());
    }
    #[test]
    fn pipeline_and_activity() {
        let c = db::open_memory().unwrap();
        let id = upsert_prospect(&c, sample()).unwrap();
        set_stage(&c, id, "qualified").unwrap();
        assert!(set_stage(&c, id, "bogus").is_err());
        assert_eq!(list_activities(&c, id).unwrap().len(), 1);
        let f = Filter { stage: Some("qualified".into()), ..Default::default() };
        assert_eq!(list_prospects(&c, &f).unwrap().len(), 1);
        delete_prospect(&c, id).unwrap();
        assert!(list_activities(&c, id).unwrap().is_empty());
    }
}
