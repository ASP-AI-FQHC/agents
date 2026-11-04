use rusqlite::{params, Connection};
use crate::Result;

const SUFFIXES: &[&str] = &["inc", "llc", "corp", "corporation", "co", "company", "ltd", "lp", "llp", "incorporated", "the"];

pub fn norm_name(s: &str) -> String {
    let cleaned: String = s.to_lowercase().chars().map(|c| if c.is_alphanumeric() { c } else { ' ' }).collect();
    cleaned.split_whitespace().filter(|w| !SUFFIXES.contains(w)).collect::<Vec<_>>().join(" ")
}
pub fn domain(url: &str) -> Option<String> {
    let u = url.trim().to_lowercase();
    let u = u.trim_start_matches("https://").trim_start_matches("http://").trim_start_matches("www.");
    let d = u.split(['/', '?', '#']).next()?.trim();
    if d.contains('.') { Some(d.to_string()) } else { None }
}
pub fn norm_addr(s: &str) -> String {
    let l = s.to_lowercase();
    let mut out = Vec::new();
    for w in l.split(|c: char| !c.is_alphanumeric()).filter(|w| !w.is_empty()) {
        out.push(match w { "street" => "st", "avenue" => "ave", "road" => "rd", "drive" => "dr", "boulevard" => "blvd", "highway" => "hwy", "suite" => "ste", o => o });
    }
    out.join(" ")
}

pub struct Candidate<'a> { pub name: &'a str, pub website: Option<&'a str>, pub address: Option<&'a str>, pub zip: Option<&'a str>, pub city: Option<&'a str>,
    pub source: &'a str, pub source_id: Option<&'a str> }

/// Existing prospect that matches by provider id, domain, name+zip/city, or address+zip.
pub fn find(c: &Connection, k: &Candidate) -> Result<Option<i64>> {
    if let Some(sid) = k.source_id {
        if let Ok(id) = c.query_row("SELECT id FROM prospects WHERE source=? AND source_id=?", params![k.source, sid], |r| r.get(0)) { return Ok(Some(id)); }
    }
    let mut st = c.prepare("SELECT id,name,website,address,zip,city FROM prospects")?;
    let rows = st.query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?, r.get::<_, Option<String>>(2)?, r.get::<_, Option<String>>(3)?, r.get::<_, Option<String>>(4)?, r.get::<_, Option<String>>(5)?)))?;
    let nn = norm_name(k.name);
    let kd = k.website.and_then(domain);
    let ka = k.address.map(norm_addr).filter(|a| !a.is_empty());
    for row in rows {
        let (id, name, web, addr, zip, city) = row?;
        if kd.is_some() && kd == web.as_deref().and_then(domain) { return Ok(Some(id)); }
        let same_name = !nn.is_empty() && nn == norm_name(&name);
        let same_place = (k.zip.is_some() && k.zip == zip.as_deref()) || (k.zip.is_none() && k.city.is_some() && k.city.map(str::to_lowercase) == city.as_deref().map(str::to_lowercase));
        if same_name && same_place { return Ok(Some(id)); }
        if ka.is_some() && k.zip.is_some() && k.zip == zip.as_deref() && ka == addr.as_deref().map(norm_addr) && same_name { return Ok(Some(id)); }
    }
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn normalization() {
        assert_eq!(norm_name("The Acme Fabrication, Inc."), "acme fabrication");
        assert_eq!(domain("https://www.Acme.com/about?x=1").as_deref(), Some("acme.com"));
        assert_eq!(norm_addr("100 Main Street, Suite 4"), "100 main st ste 4");
    }
}
