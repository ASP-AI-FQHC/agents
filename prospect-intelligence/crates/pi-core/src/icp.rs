//! Editable ICP + 100-point scoring. Missing or unverified data never earns full points.
//! Every point is explained; completeness is reported separately from fit.
use crate::{dedupe, models::*, naics};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Weights { pub industry: i64, pub size: i64, pub geo: i64, pub complexity: i64, pub signals: i64, pub services: i64, pub contact: i64 }
impl Default for Weights { fn default() -> Self { Self { industry: 20, size: 15, geo: 10, complexity: 20, signals: 15, services: 10, contact: 10 } } }

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Icp { pub weights: Weights, pub tier_a: i64, pub tier_b: i64, pub tier_c: i64, pub min_employees: i64, pub max_employees: i64,
    pub states: Vec<String>, pub counties: Vec<String> }
impl Default for Icp { fn default() -> Self { Self { weights: Weights::default(), tier_a: 80, tier_b: 60, tier_c: 40, min_employees: 10, max_employees: 500,
    states: vec!["MO".into(), "IL".into()], counties: vec![] } } }

impl Icp {
    pub fn validate(&self) -> Result<(), String> {
        let w = &self.weights;
        let sum = w.industry + w.size + w.geo + w.complexity + w.signals + w.services + w.contact;
        if [w.industry, w.size, w.geo, w.complexity, w.signals, w.services, w.contact].iter().any(|x| *x < 0) { return Err("weights must be ≥ 0".into()); }
        if sum != 100 { return Err(format!("weights must total 100 (currently {sum})")); }
        if !(self.tier_a > self.tier_b && self.tier_b > self.tier_c && self.tier_c >= 0 && self.tier_a <= 100) { return Err("thresholds must descend: A > B > C".into()); }
        if self.min_employees > self.max_employees { return Err("min employees exceeds max".into()); }
        Ok(())
    }
    pub fn tier(&self, s: i64) -> &'static str { if s >= self.tier_a { "A" } else if s >= self.tier_b { "B" } else if s >= self.tier_c { "C" } else { "Low" } }
}

pub struct Inputs<'a> { pub p: &'a Prospect, pub contacts: &'a [Contact], pub signals: usize }
#[derive(Debug, Serialize)]
pub struct Score { pub total: i64, pub tier: &'static str, pub completeness: i64, pub lines: Vec<String> }

fn pts(weight: i64, frac: f64) -> i64 { (weight as f64 * frac).round() as i64 }

pub fn completeness(p: &Prospect, contacts: &[Contact]) -> i64 {
    let has = |o: &Option<String>| o.as_deref().map_or(false, |s| !s.trim().is_empty());
    let checks = [has(&p.address), has(&p.website), has(&p.phone), has(&p.naics), p.employees_est.is_some(), has(&p.revenue_range),
        has(&p.description), p.facilities.is_some(), !contacts.is_empty(), p.year_established.is_some()];
    (checks.iter().filter(|c| **c).count() as i64) * 100 / checks.len() as i64
}

pub fn score(icp: &Icp, i: &Inputs) -> Score {
    let (p, w) = (i.p, &icp.weights);
    let mut lines = Vec::new();
    let mut total = 0;
    let mut add = |cat: &str, weight: i64, frac: f64, why: String| { let v = pts(weight, frac); total += v; lines.push(format!("{cat}: {v}/{weight} — {why}")); };

    // Industry
    let (f, why) = match p.naics.as_deref().filter(|n| !n.is_empty()) {
        Some(n) if naics::in_target_sectors(n) => (1.0, format!("NAICS {n} is in a target sector")),
        Some(n) => (0.0, format!("NAICS {n} is outside target sectors")),
        None if ["manufacturing", "wholesale", "warehousing", "industrial", "transportation"].contains(&p.vertical.as_str()) =>
            (0.5, format!("{} (classified from source tags; NAICS not verified)", p.vertical)),
        None => (0.0, "industry not available".into()),
    };
    add("Industry fit", w.industry, f, why);

    // Size
    let (f, why) = match p.employees_est {
        Some(n) => {
            let src = p.employees_source.as_deref().unwrap_or("unknown source");
            let base = if n >= icp.min_employees && n <= icp.max_employees { 1.0 } else { 0.2 };
            let conf = if src.to_lowercase().contains("estimate") { 0.6 } else { 1.0 };
            (base * conf, format!("{n} employees (source: {src}); target {}–{}", icp.min_employees, icp.max_employees))
        }
        None => (0.0, "employee count Not available".into()),
    };
    add("Company size", w.size, f, why);

    // Geography
    let st_ok = p.state.as_deref().map_or(false, |s| icp.states.iter().any(|x| x.eq_ignore_ascii_case(s)));
    let (f, why) = if !st_ok { (0.0, "state not in territory or unknown".to_string()) }
        else if icp.counties.is_empty() || p.county.as_deref().map_or(false, |c| icp.counties.iter().any(|x| x.eq_ignore_ascii_case(c))) { (1.0, "in configured territory".into()) }
        else { (0.6, "state in territory; county not confirmed".into()) };
    add("Geographic fit", w.geo, f, why);

    // Complexity (documented facts only)
    let mut f = 0.0; let mut parts = vec![];
    match p.facilities { Some(n) if n >= 3 => { f += 0.5; parts.push(format!("{n} facilities")); } Some(2) => { f += 0.3; parts.push("2 facilities".into()); } _ => {} }
    if p.employees_est.map_or(false, |n| n >= 50) { f += 0.25; parts.push("50+ employees".into()); }
    if p.description.as_deref().map_or(false, |d| !d.is_empty()) || p.products.as_deref().map_or(false, |d| !d.is_empty()) { f += 0.25; parts.push("operations documented".into()); }
    add("Operational/IT complexity", w.complexity, f, if parts.is_empty() { "no documented complexity indicators".into() } else { parts.join(", ") });

    // Signals
    let n = i.signals.min(3);
    add("Buying/growth signals", w.signals, n as f64 / 3.0, if i.signals == 0 { "no sourced signals on file".into() } else { format!("{} sourced signal(s)", i.signals) });

    // Service opportunities (each needs documented support)
    let mut opp = vec![];
    if p.facilities.map_or(false, |n| n >= 2) { opp.push("facility connectivity/VoIP"); }
    if p.employees_est.map_or(false, |n| n >= icp.min_employees && n <= icp.max_employees) { opp.push("managed/co-managed IT"); }
    if i.signals > 0 { opp.push("cloud & continuity planning"); }
    if p.naics.as_deref().map_or(false, |n| n.starts_with("31") || n.starts_with("32") || n.starts_with("33") || n.starts_with("493")) { opp.push("operations-systems support"); }
    add("Service opportunities", w.services, opp.len() as f64 / 4.0, if opp.is_empty() { "none supported by documented facts".into() } else { opp.join(", ") });

    // Contact readiness
    let vc = i.contacts.iter().filter(|c| c.verified);
    let (f, why) = if vc.clone().any(|c| c.email.is_some() || c.phone.is_some()) { (1.0, "verified decision-maker with direct contact info") }
        else if vc.clone().next().is_some() { (0.6, "verified contact without direct info") }
        else if !i.contacts.is_empty() { (0.3, "unverified lead(s) only") } else { (0.0, "no contacts") };
    add("Contact readiness", w.contact, f, why.into());

    let total = total.min(100);
    Score { total, tier: icp.tier(total), completeness: completeness(p, i.contacts), lines }
}

#[allow(dead_code)]
fn _use(_: &str) -> Option<String> { dedupe::domain("") }

#[cfg(test)]
mod tests {
    use super::*;
    fn base() -> Prospect { Prospect { name: "Acme".into(), vertical: "manufacturing".into(), state: Some("MO".into()), county: Some("St. Louis".into()), ..Default::default() } }
    fn sc(p: &Prospect, c: &[Contact], s: usize) -> Score { score(&Icp::default(), &Inputs { p, contacts: c, signals: s }) }

    #[test]
    fn unverified_never_full_points() {
        let s = sc(&base(), &[], 0);
        assert_eq!(s.total, 20 / 2 + 10); // half industry (tag only) + geo
        assert_eq!(s.tier, "Low");
    }
    #[test]
    fn strong_prospect_is_priority_a() {
        let mut p = base();
        p.naics = Some("332710".into()); p.employees_est = Some(120); p.employees_source = Some("Provider X".into());
        p.facilities = Some(3); p.description = Some("Contract metal fabrication".into());
        let c = Contact { id: 1, prospect_id: 1, name: "Pat".into(), title: Some("President".into()), email: Some("p@a.com".into()), phone: None, linkedin: None,
            source: "provider".into(), verified: true, profile_url: None, verified_on: None };
        let s = sc(&p, &[c], 3);
        assert_eq!(s.total, 100); assert_eq!(s.tier, "A"); assert_eq!(s.lines.len(), 7);
    }
    #[test]
    fn estimates_discounted_and_completeness_separate() {
        let mut p = base(); p.employees_est = Some(100); p.employees_source = Some("estimate (csv)".into());
        let s = sc(&p, &[], 0);
        assert!(s.lines[1].starts_with("Company size: 9/15"));
        assert_eq!(s.completeness, 10);
    }
    #[test]
    fn weights_validation() {
        let mut i = Icp::default(); assert!(i.validate().is_ok());
        i.weights.industry = 30; assert!(i.validate().is_err());
        let mut i = Icp::default(); i.tier_b = 90; assert!(i.validate().is_err());
    }
    #[test]
    fn adjustable_thresholds() { let mut i = Icp::default(); i.tier_a = 30; i.tier_b = 20; i.tier_c = 10; assert_eq!(i.tier(35), "A"); }
}
