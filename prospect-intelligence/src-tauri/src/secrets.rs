//! OS-backed credential storage (Windows Credential Manager). Values never go back to the UI.
use keyring::Entry;

const SERVICE: &str = "com.itarchitechs.prospectintelligence";
/// Providers whose credentials the app can hold. Only those marked implemented are used by search.
pub const KNOWN: &[&str] = &["google_places", "apollo", "hunter", "zoominfo", "gateway_token"];

fn entry(name: &str) -> Result<Entry, String> {
    if !KNOWN.contains(&name) { return Err(format!("unknown credential '{name}'")); }
    Entry::new(SERVICE, name).map_err(|e| e.to_string())
}
pub fn set(name: &str, value: &str) -> Result<(), String> {
    if value.trim().is_empty() { return Err("empty value".into()); }
    entry(name)?.set_password(value).map_err(|e| e.to_string())
}
pub fn has(name: &str) -> bool { entry(name).ok().and_then(|e| e.get_password().ok()).is_some() }
pub fn delete(name: &str) -> Result<(), String> {
    match entry(name)?.delete_credential() { Ok(()) | Err(keyring::Error::NoEntry) => Ok(()), Err(e) => Err(e.to_string()) }
}
#[allow(dead_code)]
pub fn get(name: &str) -> Option<String> { entry(name).ok()?.get_password().ok() }
