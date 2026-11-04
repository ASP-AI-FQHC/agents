//! Transparent, rule-based fit scoring. Every point is explained; no invented data.
use crate::models::Prospect;

const TARGET_VERTICALS: &[(&str, i64, &str)] = &[
    ("manufacturing", 25, "Manufacturing: OT/IT convergence, ransomware exposure, uptime-critical"),
    ("wholesale", 22, "Wholesale/distribution: ERP, EDI, and supply-chain compliance needs"),
    ("warehousing", 22, "Warehousing/logistics: WMS uptime, network and device management"),
    ("industrial", 20, "Industrial supplier: multi-site connectivity and ERP dependence"),
    ("transportation", 15, "Transportation: fleet systems, compliance and telematics security"),
];

pub fn score(p: &Prospect) -> (i64, Vec<String>) {
    let mut pts = 0;
    let mut why = Vec::new();
    if let Some((_, v, r)) = TARGET_VERTICALS.iter().find(|(k, _, _)| *k == p.vertical) {
        pts += v; why.push(r.to_string());
    }
    if let Some(n) = p.employees_est {
        let (v, r) = match n {
            0..=9 => (0, None),
            10..=24 => (10, Some("10–24 employees: likely lacks in-house IT")),
            25..=250 => (25, Some("25–250 employees: sweet spot for managed IT + security")),
            251..=1000 => (15, Some("251–1,000 employees: co-managed IT opportunity")),
            _ => (5, Some("1,000+ employees: likely has internal IT; narrower fit")),
        };
        pts += v;
        if let Some(r) = r { why.push(format!("{r} (source: {})", p.employees_source.as_deref().unwrap_or("unknown"))); }
    }
    if p.website.is_some() { pts += 5; why.push("Website available for outreach research".into()); }
    if p.phone.is_some() { pts += 5; why.push("Phone number on file".into()); }
    if p.county.is_some() { pts += 5; why.push("Within a configured service territory".into()); }
    (pts.min(100), why)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn manufacturer_mid_size_scores_high() {
        let p = Prospect { vertical: "manufacturing".into(), employees_est: Some(80), employees_source: Some("manual".into()),
            website: Some("x".into()), phone: Some("1".into()), county: Some("St. Louis".into()), ..Default::default() };
        let (s, r) = score(&p);
        assert_eq!(s, 65); assert_eq!(r.len(), 5);
    }
    #[test]
    fn unknown_scores_zero() { assert_eq!(score(&Prospect::default()).0, 0); }
}
