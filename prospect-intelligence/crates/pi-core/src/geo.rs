use crate::{models::Region, Error, Result};
use rusqlite::{params, Connection};

pub const MISSOURI_COUNTIES: &[&str] = &[
    "Adair","Andrew","Atchison","Audrain","Barry","Barton","Bates","Benton","Bollinger","Boone","Buchanan","Butler",
    "Caldwell","Callaway","Camden","Cape Girardeau","Carroll","Carter","Cass","Cedar","Chariton","Christian","Clark",
    "Clay","Clinton","Cole","Cooper","Crawford","Dade","Dallas","Daviess","DeKalb","Dent","Douglas","Dunklin",
    "Franklin","Gasconade","Gentry","Greene","Grundy","Harrison","Henry","Hickory","Holt","Howard","Howell","Iron",
    "Jackson","Jasper","Jefferson","Johnson","Knox","Laclede","Lafayette","Lawrence","Lewis","Lincoln","Linn",
    "Livingston","McDonald","Macon","Madison","Maries","Marion","Mercer","Miller","Mississippi","Moniteau","Monroe",
    "Montgomery","Morgan","New Madrid","Newton","Nodaway","Oregon","Osage","Ozark","Pemiscot","Perry","Pettis",
    "Phelps","Pike","Platte","Polk","Pulaski","Putnam","Ralls","Randolph","Ray","Reynolds","Ripley","St. Charles",
    "St. Clair","St. Francois","St. Louis","Ste. Genevieve","Saline","Schuyler","Scotland","Scott","Shannon","Shelby",
    "Stoddard","Stone","Sullivan","Taney","Texas","Vernon","Warren","Washington","Wayne","Webster","Worth","Wright",
];
/// Independent city, treated as a county-equivalent.
pub const MISSOURI_INDEPENDENT_CITIES: &[&str] = &["St. Louis City"];

/// Southern Illinois / Metro East counties enabled by default.
pub const IL_DEFAULT: &[&str] = &[
    "St. Clair","Madison","Monroe","Randolph","Clinton","Washington","Marion","Jefferson","Jackson","Williamson",
    "Franklin","Perry","Union",
];
/// Additional Illinois counties available for admins to enable.
pub const IL_OPTIONAL: &[&str] = &[
    "Alexander","Bond","Clay","Crawford","Edwards","Effingham","Fayette","Gallatin","Hamilton","Hardin","Jasper",
    "Jersey","Johnson","Lawrence","Macoupin","Massac","Pope","Pulaski","Richland","Saline","Wabash","Wayne","White",
];

pub(crate) fn seed(c: &Connection) -> Result<()> {
    let mut ins = c.prepare("INSERT OR IGNORE INTO regions(state,name,kind,enabled) VALUES(?,?,?,?)")?;
    for n in MISSOURI_COUNTIES { ins.execute(params!["MO", n, "county", 1])?; }
    for n in MISSOURI_INDEPENDENT_CITIES { ins.execute(params!["MO", n, "county", 1])?; }
    for n in IL_DEFAULT { ins.execute(params!["IL", n, "county", 1])?; }
    for n in IL_OPTIONAL { ins.execute(params!["IL", n, "county", 0])?; }
    Ok(())
}

pub fn list_regions(c: &Connection, state: Option<&str>) -> Result<Vec<Region>> {
    let mut st = c.prepare(
        "SELECT id,state,name,kind,enabled,is_custom FROM regions WHERE (?1 IS NULL OR state=?1) ORDER BY state,name")?;
    let rows = st.query_map([state], |r| Ok(Region {
        id: r.get(0)?, state: r.get(1)?, name: r.get(2)?, kind: r.get(3)?,
        enabled: r.get::<_, i64>(4)? != 0, is_custom: r.get::<_, i64>(5)? != 0,
    }))?;
    Ok(rows.collect::<std::result::Result<_, _>>()?)
}

pub fn set_enabled(c: &Connection, id: i64, enabled: bool) -> Result<()> {
    c.execute("UPDATE regions SET enabled=? WHERE id=?", params![enabled as i64, id])?;
    Ok(())
}

/// Administrator adds a county (IL or MO only).
pub fn add_county(c: &Connection, state: &str, name: &str) -> Result<()> {
    let state = state.to_uppercase();
    if state != "IL" && state != "MO" { return Err(Error::Invalid("state must be IL or MO".into())); }
    let name = name.trim();
    if name.is_empty() { return Err(Error::Invalid("county name required".into())); }
    c.execute(
        "INSERT INTO regions(state,name,kind,enabled,is_custom) VALUES(?,?,'county',1,1)
         ON CONFLICT(state,name,kind) DO UPDATE SET enabled=1",
        params![state, name])?;
    Ok(())
}

/// Administrator removes a custom county; seeded counties are only disabled.
pub fn remove_county(c: &Connection, id: i64) -> Result<()> {
    c.execute("DELETE FROM regions WHERE id=? AND is_custom=1", [id])?;
    c.execute("UPDATE regions SET enabled=0 WHERE id=?", [id])?;
    Ok(())
}

pub fn valid_zip(z: &str) -> bool { z.len() == 5 && z.bytes().all(|b| b.is_ascii_digit()) }

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db;

    #[test]
    fn counts() {
        assert_eq!(MISSOURI_COUNTIES.len(), 114);
        let c = db::open_memory().unwrap();
        assert_eq!(list_regions(&c, Some("MO")).unwrap().len(), 115);
        let il = list_regions(&c, Some("IL")).unwrap();
        assert_eq!(il.iter().filter(|r| r.enabled).count(), IL_DEFAULT.len());
    }

    #[test]
    fn admin_add_remove() {
        let c = db::open_memory().unwrap();
        add_county(&c, "il", "Cook").unwrap();
        let id = list_regions(&c, Some("IL")).unwrap().into_iter().find(|r| r.name == "Cook").unwrap().id;
        remove_county(&c, id).unwrap();
        assert!(list_regions(&c, Some("IL")).unwrap().iter().all(|r| r.name != "Cook"));
        assert!(add_county(&c, "TX", "x").is_err());
    }
    #[test]
    fn zip() { assert!(valid_zip("63101")); assert!(!valid_zip("6310")); }
}
