//! Platform-independent core: storage, geography, scoring.
pub mod db;
pub mod geo;
pub mod models;
pub mod repo;
pub mod icp;
pub mod naics;
pub mod dedupe;
pub mod csvio;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("database: {0}")]
    Db(#[from] rusqlite::Error),
    #[error("json: {0}")]
    Json(#[from] serde_json::Error),
    #[error("{0}")]
    Invalid(String),
}
pub type Result<T> = std::result::Result<T, Error>;
