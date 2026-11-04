//! CSV import (authorized lead lists) and export (respecting per-record export permission).
use crate::{models::*, repo, Result};
use rusqlite::Connection;
use serde::Serialize;

#[derive(Debug, Default, Serialize)]
pub struct ImportSummary { pub inserted: usize, pub merged: usize, pub skipped: usize, pub errors: Vec<String> }

fn idx(h: &[String], names: &[&str]) -> Option<usize> { h.iter().position(|x| names.contains(&x.as_str())) }
fn norm_header(s: &str) -> String { s.trim().to_lowercase().replace([' ', '-'], "_") }
fn num(s: &str) -> Option<i64> { s.trim().replace(',', "").parse().ok() }

pub fn import(c: &Connection, data: &str, filename: &str, state_default: Option<&str>) -> Result<ImportSummary> {
    let mut rdr = csv::ReaderBuilder::new().flexible(true).trim(csv::Trim::All).from_reader(data.as_bytes());
    let headers: Vec<String> = rdr.headers().map_err(|e| crate::Error::Invalid(e.to_string()))?.iter().map(norm_header).collect();
    let name_i = idx(&headers, &["name", "company", "company_name", "business_name"]).ok_or_else(|| crate::Error::Invalid("CSV needs a company name column (name/company)".into()))?;
    let (web, phone, addr, city, county, state, zip) = (idx(&headers, &["website", "url", "domain"]), idx(&headers, &["phone", "main_phone"]),
        idx(&headers, &["address", "street", "street_address"]), idx(&headers, &["city"]), idx(&headers, &["county"]), idx(&headers, &["state"]),
        idx(&headers, &["zip", "zip_code", "postal_code"]));
    let (naics_i, sic_i, emp, rev, legal, fac, year, desc) = (idx(&headers, &["naics", "naics_code"]), idx(&headers, &["sic", "sic_code"]),
        idx(&headers, &["employees", "employee_count"]), idx(&headers, &["revenue", "revenue_range"]), idx(&headers, &["legal_name"]),
        idx(&headers, &["facilities", "locations"]), idx(&headers, &["year_established", "founded"]), idx(&headers, &["description"]));
    let (cn, ct, ce, cp) = (idx(&headers, &["contact_name"]), idx(&headers, &["contact_title"]), idx(&headers, &["contact_email"]), idx(&headers, &["contact_phone"]));
    let mut out = ImportSummary::default();
    let src = format!("csv:{filename}");
    for (n, rec) in rdr.records().enumerate() {
        let rec = match rec { Ok(r) => r, Err(e) => { out.errors.push(format!("row {}: {e}", n + 2)); out.skipped += 1; continue } };
        let g = |i: Option<usize>| i.and_then(|i| rec.get(i)).map(str::trim).filter(|s| !s.is_empty()).map(str::to_string);
        let Some(name) = g(Some(name_i)) else { out.skipped += 1; continue };
        let employees = g(emp).and_then(|s| num(&s));
        let p = Prospect { name, website: g(web), phone: g(phone), address: g(addr), city: g(city), county: g(county),
            state: g(state).or_else(|| state_default.map(str::to_string)), zip: g(zip), naics: g(naics_i), sic: g(sic_i), revenue_range: g(rev), legal_name: g(legal),
            employees_est: employees, employees_source: employees.map(|_| format!("{src} (user-supplied)")), facilities: g(fac).and_then(|s| num(&s)),
            year_established: g(year).and_then(|s| num(&s)), description: g(desc), vertical: "other".into(), source: src.clone(), source_id: None,
            origin: "imported".into(), export_ok: true, ..Default::default() };
        match repo::ingest(c, p) {
            Ok(r) => {
                if r.merged { out.merged += 1 } else { out.inserted += 1 }
                if let Some(name) = g(cn) {
                    repo::add_contact(c, &Contact { id: 0, prospect_id: r.id, name, title: g(ct), email: g(ce), phone: g(cp), linkedin: None, source: src.clone(),
                        verified: false, profile_url: None, verified_on: None })?;
                }
            }
            Err(e) => { out.errors.push(format!("row {}: {e}", n + 2)); out.skipped += 1 }
        }
    }
    Ok(out)
}

/// Excel-friendly CSV (UTF-8 BOM). Records flagged `export_ok = false` are excluded and counted.
pub fn export(rows: &[Prospect]) -> (String, usize) {
    let mut w = csv::Writer::from_writer(vec![]);
    let _ = w.write_record(["Company", "Legal name", "Industry", "NAICS", "SIC", "Address", "City", "County", "State", "ZIP", "Website", "Phone", "Employees",
        "Employee source", "Revenue range", "Stage", "Score", "Tier", "Completeness %", "Data origin", "Source", "Source URL"]);
    let mut skipped = 0;
    for p in rows {
        if !p.export_ok { skipped += 1; continue; }
        let o = |v: &Option<String>| v.clone().unwrap_or_else(|| "Not available".into());
        let _ = w.write_record([p.name.clone(), o(&p.legal_name), p.vertical.clone(), o(&p.naics), o(&p.sic), o(&p.address), o(&p.city), o(&p.county), o(&p.state),
            o(&p.zip), o(&p.website), o(&p.phone), p.employees_est.map_or("Not available".into(), |n| n.to_string()), o(&p.employees_source), o(&p.revenue_range),
            p.stage.clone(), p.score.to_string(), p.tier.clone(), p.completeness.to_string(), p.origin.clone(), p.source.clone(), o(&p.source_url)]);
    }
    let body = String::from_utf8(w.into_inner().unwrap_or_default()).unwrap_or_default();
    (format!("\u{feff}{body}"), skipped)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db;
    const CSV: &str = "Company Name,Website,City,State,ZIP,NAICS,Employees,Contact Name,Contact Email\n\
        Acme Fab,acme.com,Maryland Heights,MO,63043,332710,\"1,200\",Pat Lee,pat@acme.com\n\
        Acme Fab Inc,https://www.acme.com,,MO,63043,,,,\n,,,,,,,,\n\
        Delta Foods,,Belleville,IL,62220,311999,45,,\n";
    #[test]
    fn import_dedupes_and_labels() {
        let c = db::open_memory().unwrap();
        let s = import(&c, CSV, "leads.csv", None).unwrap();
        assert_eq!((s.inserted, s.merged, s.skipped), (2, 1, 1), "{s:?}");
        let all = repo::list_prospects(&c, &repo::Filter::default()).unwrap();
        let a = all.iter().find(|p| p.name == "Acme Fab").unwrap();
        assert_eq!(a.employees_est, Some(1200)); assert_eq!(a.origin, "imported");
        assert!(a.employees_source.as_deref().unwrap().contains("user-supplied"));
        assert!(!repo::list_contacts(&c, a.id).unwrap()[0].verified, "imported contacts are unverified leads");
    }
    #[test]
    fn missing_name_column_errors() { let c = db::open_memory().unwrap(); assert!(import(&c, "a,b\n1,2\n", "x.csv", None).is_err()); }
    #[test]
    fn export_respects_license_flag_and_marks_missing() {
        let c = db::open_memory().unwrap();
        import(&c, CSV, "leads.csv", None).unwrap();
        let mut rows = repo::list_prospects(&c, &repo::Filter::default()).unwrap();
        rows[0].export_ok = false;
        let (out, skipped) = export(&rows);
        assert_eq!(skipped, 1); assert!(out.starts_with('\u{feff}')); assert!(out.contains("Not available"));
    }
}
