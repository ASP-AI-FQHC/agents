# Administrator guide

## Install (partners)
1. Download `ITArchitechs-Prospect-Intelligence-Setup.exe` from the release link you were given.
2. Run it. If Windows SmartScreen warns "unrecognized app", the build is **unsigned** — choose *More info → Run anyway* only if you trust the source. (See Code signing.)
3. Launch from the Start Menu. No Node, Rust or Python needed. WebView2 is preinstalled on Windows 10/11.

## Data
- Everything is stored locally in `%APPDATA%\com.itarchitechs.prospectintelligence\prospects.db`. Installed copies do not share data.
- Settings → *Back up database* / *Restore* / *Delete all prospect data*.

## Sources and costs
| Source | Cost | What it provides | Status |
|---|---|---|---|
| OpenStreetMap (Nominatim/Overpass) | Free | Name, location, phone, website only. Incomplete. Public-service usage limits apply; keep volume low. | Implemented, untested live |
| CSV import | Free | Whatever you supply (NAICS, employees, contacts…). You must hold the rights. | Implemented, tested |
| Commercial provider (Data Axle, Treg, …) | Paid | Employees, NAICS, revenue, contacts | **Not implemented** — needs provider API documentation |
| AI profiling | Paid per use | — | **Not implemented**; will require per-company user approval |

## Scoring
Settings → Ideal Customer Profile. Weights must total 100; thresholds must descend A > B > C. Saving re-scores every prospect.

## Code signing
Unsigned installers trigger SmartScreen warnings and cannot be trusted for auto-update. To sign: obtain an OV/EV code-signing certificate (or Azure Trusted Signing), store it as GitHub Actions secrets, and set `bundle.windows.certificateThumbprint` / a signing command in `tauri.conf.json`. Signed *updates* additionally need `tauri-plugin-updater` with a Tauri update keypair (`npx tauri signer generate`); not yet wired.

## Distribution
- Push a tag `pi-v0.1.0` → workflow builds on Windows and creates a **draft** GitHub Release with the installers + SHA256SUMS.
- A **private** repository's release URL needs a GitHub login; partners cannot download anonymously. Options: (a) publish the release assets to a separate public repo or company-controlled storage, or (b) host on a company site. `https://www.itarchitechs.com/downloads/` is a *proposed* location and does not exist yet.
