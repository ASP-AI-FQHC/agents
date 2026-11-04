//! NAICS-driven industry filtering. Broad sector membership alone never qualifies a prospect.
use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub struct Category { pub id: &'static str, pub label: &'static str, pub group: &'static str, pub prefixes: &'static [&'static str] }

pub const SECTORS: &[Category] = &[
    Category { id: "sector-mfg", label: "Manufacturing (31–33)", group: "Sector", prefixes: &["31", "32", "33"] },
    Category { id: "sector-wholesale", label: "Wholesale Trade (42)", group: "Sector", prefixes: &["42"] },
    Category { id: "sector-transport", label: "Transportation & Warehousing (48–49)", group: "Sector", prefixes: &["48", "49"] },
];

pub const CATEGORIES: &[Category] = &[
    Category { id: "food-mfg", label: "Food manufacturing", group: "Manufacturing", prefixes: &["311", "312"] },
    Category { id: "metal-fab", label: "Metal fabrication", group: "Manufacturing", prefixes: &["332"] },
    Category { id: "machinery", label: "Machinery manufacturing", group: "Manufacturing", prefixes: &["333"] },
    Category { id: "plastics-rubber", label: "Plastics and rubber", group: "Manufacturing", prefixes: &["326"] },
    Category { id: "auto-components", label: "Automotive components", group: "Manufacturing", prefixes: &["3363"] },
    Category { id: "chemical", label: "Chemical manufacturing", group: "Manufacturing", prefixes: &["325"] },
    Category { id: "industrial-equip", label: "Industrial equipment", group: "Manufacturing", prefixes: &["3332", "3339"] },
    Category { id: "electrical-equip", label: "Electrical equipment", group: "Manufacturing", prefixes: &["335"] },
    Category { id: "medical-equip", label: "Medical equipment manufacturing", group: "Manufacturing", prefixes: &["3391"] },
    Category { id: "construction-materials", label: "Construction materials", group: "Manufacturing", prefixes: &["327"] },
    Category { id: "industrial-dist", label: "Industrial distributors", group: "Distribution", prefixes: &["4238", "4246"] },
    Category { id: "food-bev-dist", label: "Food and beverage distributors", group: "Distribution", prefixes: &["4244", "4248"] },
    Category { id: "wholesale-suppliers", label: "Wholesale suppliers", group: "Distribution", prefixes: &["42"] },
    Category { id: "warehousing", label: "Warehousing", group: "Logistics", prefixes: &["493"] },
    Category { id: "logistics-freight", label: "Logistics and freight operators", group: "Logistics", prefixes: &["484", "488", "492"] },
];

pub fn by_id(id: &str) -> Option<&'static Category> { SECTORS.iter().chain(CATEGORIES).find(|c| c.id == id) }

/// True when `naics` starts with any prefix of any selected category. Empty selection matches everything.
pub fn matches(naics: Option<&str>, selected: &[String]) -> bool {
    if selected.is_empty() { return true; }
    let Some(n) = naics.map(str::trim).filter(|n| !n.is_empty()) else { return false };
    selected.iter().filter_map(|id| by_id(id)).any(|c| c.prefixes.iter().any(|p| n.starts_with(p)))
}

/// Broad target sectors (used by ICP industry-fit).
pub fn in_target_sectors(naics: &str) -> bool { SECTORS.iter().any(|s| s.prefixes.iter().any(|p| naics.starts_with(p))) }

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn granular_and_sector() {
        let s = |x: &[&str]| x.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert!(matches(Some("332710"), &s(&["metal-fab"])));
        assert!(!matches(Some("332710"), &s(&["machinery"])));
        assert!(matches(Some("423830"), &s(&["industrial-dist"])));
        assert!(matches(Some("493110"), &s(&["sector-transport"])));
        assert!(!matches(None, &s(&["metal-fab"])), "unknown NAICS must not match a filter");
        assert!(matches(None, &[]));
    }
}
