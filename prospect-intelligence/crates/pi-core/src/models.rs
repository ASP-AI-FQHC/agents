use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Region {
    pub id: i64,
    pub state: String,
    pub name: String,
    pub kind: String,
    pub enabled: bool,
    pub is_custom: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct Prospect {
    pub id: i64,
    pub name: String,
    pub website: Option<String>,
    pub phone: Option<String>,
    pub address: Option<String>,
    pub city: Option<String>,
    pub county: Option<String>,
    pub state: Option<String>,
    pub zip: Option<String>,
    pub lat: Option<f64>,
    pub lon: Option<f64>,
    pub vertical: String,
    pub description: Option<String>,
    pub employees_est: Option<i64>,
    pub employees_source: Option<String>,
    pub source: String,
    pub source_id: Option<String>,
    pub source_url: Option<String>,
    pub stage: String,
    pub score: i64,
    pub score_reasons: Vec<String>,
    pub next_action: Option<String>,
    pub next_action_due: Option<String>,
    pub legal_name: Option<String>,
    pub naics: Option<String>,
    pub sic: Option<String>,
    pub revenue_range: Option<String>,
    pub year_established: Option<i64>,
    pub parent_company: Option<String>,
    pub facilities: Option<i64>,
    pub products: Option<String>,
    pub owner: Option<String>,
    pub tags: Option<String>,
    /// demo | imported | manual | live
    pub origin: String,
    pub export_ok: bool,
    pub tier: String,
    pub completeness: i64,
    pub updated_at: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Contact {
    pub id: i64,
    pub prospect_id: i64,
    pub name: String,
    pub title: Option<String>,
    pub email: Option<String>,
    pub phone: Option<String>,
    pub linkedin: Option<String>,
    pub source: String,
    pub verified: bool,
    pub profile_url: Option<String>,
    pub verified_on: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Activity {
    pub id: i64,
    pub prospect_id: i64,
    pub kind: String,
    pub summary: String,
    pub occurred_at: String,
}

pub const STAGES: &[&str] = &["new", "researching", "qualified", "contacted", "meeting", "proposal", "won", "lost"];
pub const VERTICALS: &[&str] = &["manufacturing", "wholesale", "warehousing", "industrial", "transportation", "other"];

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Signal { pub id: i64, pub prospect_id: i64, pub kind: String, pub summary: String, pub source: String, pub source_url: Option<String>, pub event_date: Option<String> }

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FieldSource { pub id: i64, pub prospect_id: i64, pub field: String, pub value: String, pub source: String, pub source_ref: Option<String>,
    pub retrieved_at: String, pub status: String, pub confidence: Option<String> }

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Conflict { pub field: String, pub values: Vec<FieldSource> }
