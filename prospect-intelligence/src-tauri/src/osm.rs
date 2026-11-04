//! OpenStreetMap provider (Nominatim geocoding + Overpass). Free, no key, real data.
//! Coverage is crowd-sourced: no employee counts or contacts. Respect usage policies (User-Agent, low volume).
use pi_core::models::Prospect;
use serde::Deserialize;
use serde_json::Value;

const UA: &str = "ITArchitechsProspectIntelligence/0.1 (sales prospecting; contact: itarchitechs.com)";
const NOMINATIM: &str = "https://nominatim.openstreetmap.org/search";
const OVERPASS: &str = "https://overpass-api.de/api/interpreter";

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", tag = "kind")]
pub enum Geography {
    State { state: String },
    County { state: String, county: String },
    City { state: String, city: String },
    Zip { zip: String },
    Radius { query: String, miles: f64 },
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SearchRequest { pub geography: Geography, pub verticals: Vec<String>, pub limit: Option<u32> }

fn state_name(s: &str) -> &str { match s { "MO" => "Missouri", "IL" => "Illinois", o => o } }

async fn geocode(c: &reqwest::Client, q: &[(&str, String)]) -> Result<Value, String> {
    let mut params: Vec<(&str, String)> = q.to_vec();
    params.push(("format", "jsonv2".into())); params.push(("limit", "1".into())); params.push(("countrycodes", "us".into()));
    let v: Value = c.get(NOMINATIM).query(&params).send().await.map_err(|e| e.to_string())?
        .error_for_status().map_err(|e| e.to_string())?.json().await.map_err(|e| e.to_string())?;
    v.get(0).cloned().ok_or_else(|| "location not found".to_string())
}

fn bbox(v: &Value) -> Result<String, String> {
    let b = v["boundingbox"].as_array().ok_or("no bounding box")?;
    let f: Vec<&str> = b.iter().filter_map(|x| x.as_str()).collect();
    if f.len() != 4 { return Err("bad bounding box".into()); }
    Ok(format!("{},{},{},{}", f[0], f[2], f[1], f[3])) // S,W,N,E
}

fn selectors(verticals: &[String]) -> Vec<&'static str> {
    let mut s = vec![];
    let all = verticals.is_empty();
    let has = |v: &str| all || verticals.iter().any(|x| x == v);
    if has("manufacturing") { s.extend(["[\"man_made\"=\"works\"]", "[\"industrial\"~\"factory|manufacture|machine_shop|engineering\"]", "[\"craft\"~\"metal_construction|sawmill|carpenter\"]"]); }
    if has("wholesale") { s.extend(["[\"shop\"=\"wholesale\"]", "[\"industrial\"=\"wholesale\"]"]); }
    if has("warehousing") { s.extend(["[\"industrial\"~\"warehouse|logistics|distribution\"]", "[\"office\"=\"logistics\"]"]); }
    if has("industrial") { s.extend(["[\"industrial\"~\"^(oil|chemical|mine|slaughterhouse|machine_shop|engineering)$\"]"]); }
    s.sort(); s.dedup(); s
}

fn classify(tags: &Value) -> &'static str {
    let t = |k: &str| tags[k].as_str().unwrap_or("");
    let ind = t("industrial");
    if t("shop") == "wholesale" || ind == "wholesale" { return "wholesale"; }
    if ind.contains("warehouse") || ind.contains("logistics") || ind.contains("distribution") || t("office") == "logistics" { return "warehousing"; }
    if t("man_made") == "works" || ["factory","manufacture","machine_shop","engineering"].iter().any(|k| ind.contains(k)) || !t("craft").is_empty() { return "manufacturing"; }
    let n = t("name").to_lowercase();
    if ["trucking","freight","transport","hauling"].iter().any(|k| n.contains(k)) { return "transportation"; }
    "industrial"
}

pub async fn search(req: SearchRequest) -> Result<Vec<Prospect>, String> {
    let sels = selectors(&req.verticals);
    if sels.is_empty() { return Err("select at least one supported vertical".into()); }
    let c = reqwest::Client::builder().user_agent(UA).timeout(std::time::Duration::from_secs(120)).build().map_err(|e| e.to_string())?;
    let limit = req.limit.unwrap_or(200).min(500);

    let (prefix, scope, st, county) = match &req.geography {
        Geography::State { state } => (
            format!("area[\"ISO3166-2\"=\"US-{state}\"]->.a;"), "(area.a)".to_string(), Some(state.clone()), None),
        Geography::County { state, county } => {
            let g = geocode(&c, &[("county", county.clone()), ("state", state_name(state).into()), ("country", "USA".into())]).await?;
            (String::new(), format!("({})", bbox(&g)?), Some(state.clone()), Some(county.clone()))
        }
        Geography::City { state, city } => {
            let g = geocode(&c, &[("city", city.clone()), ("state", state_name(state).into()), ("country", "USA".into())]).await?;
            (String::new(), format!("({})", bbox(&g)?), Some(state.clone()), None)
        }
        Geography::Zip { zip } => {
            if !pi_core::geo::valid_zip(zip) { return Err("ZIP must be 5 digits".into()); }
            let g = geocode(&c, &[("postalcode", zip.clone()), ("country", "USA".into())]).await?;
            let lat: f64 = g["lat"].as_str().and_then(|s| s.parse().ok()).ok_or("bad lat")?;
            let lon: f64 = g["lon"].as_str().and_then(|s| s.parse().ok()).ok_or("bad lon")?;
            (String::new(), format!("(around:8000,{lat},{lon})"), None, None)
        }
        Geography::Radius { query, miles } => {
            let g = geocode(&c, &[("q", query.clone())]).await?;
            let lat: f64 = g["lat"].as_str().and_then(|s| s.parse().ok()).ok_or("bad lat")?;
            let lon: f64 = g["lon"].as_str().and_then(|s| s.parse().ok()).ok_or("bad lon")?;
            (String::new(), format!("(around:{},{lat},{lon})", (miles.clamp(1.0, 100.0) * 1609.34) as u32), None, None)
        }
    };

    let body: String = sels.iter().map(|s| format!("nwr{s}[\"name\"]{scope};")).collect();
    let q = format!("[out:json][timeout:90];{prefix}({body});out center tags {limit};");
    let v: Value = c.post(OVERPASS).form(&[("data", q)]).send().await.map_err(|e| e.to_string())?
        .error_for_status().map_err(|e| format!("overpass: {e}"))?.json().await.map_err(|e| e.to_string())?;

    let mut out = vec![];
    for el in v["elements"].as_array().cloned().unwrap_or_default() {
        let tags = &el["tags"];
        let name = match tags["name"].as_str() { Some(n) => n.to_string(), None => continue };
        let g = |k: &str| tags[k].as_str().map(str::to_string);
        let ty = el["type"].as_str().unwrap_or("node");
        let id = el["id"].as_i64().unwrap_or(0);
        let street = match (g("addr:housenumber"), g("addr:street")) { (Some(n), Some(s)) => Some(format!("{n} {s}")), (_, s) => s };
        let state = g("addr:state").or_else(|| st.clone());
        out.push(Prospect {
            name, vertical: classify(tags).into(),
            website: g("website").or_else(|| g("contact:website")), phone: g("phone").or_else(|| g("contact:phone")),
            address: street, city: g("addr:city"), zip: g("addr:postcode"), state, county: county.clone(),
            lat: el["lat"].as_f64().or_else(|| el["center"]["lat"].as_f64()),
            lon: el["lon"].as_f64().or_else(|| el["center"]["lon"].as_f64()),
            source: "osm".into(), source_id: Some(format!("{ty}/{id}")),
            source_url: Some(format!("https://www.openstreetmap.org/{ty}/{id}")),
            ..Default::default()
        });
    }
    Ok(out)
}
