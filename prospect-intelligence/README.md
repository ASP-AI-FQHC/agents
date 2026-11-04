# IT Architechs Prospect Intelligence
*Discover. Qualify. Connect.* Windows desktop prospecting app (Tauri 2 · React/TS · Tailwind · Rust · SQLite).

## Status (v0.1 foundation)
| Area | State |
|---|---|
| SQLite storage, migrations, offline viewing | Done, unit-tested (`cargo test -p pi-core`) |
| MO (114 counties + St. Louis City) and Southern IL county admin (add/enable/remove) | Done, tested |
| Transparent fit scoring (every point explained) | Done, tested |
| Pipeline stages, contacts, activities, next actions | Done |
| Credentials in Windows Credential Manager | Coded; **untested on Windows** |
| Live search: OpenStreetMap (Nominatim + Overpass), state/county/city/ZIP/radius | Coded, compiles; **not live-verified** (sandbox blocked outbound) |
| Google Places, Apollo, Hunter, ZoomInfo, shared gateway | Credential slots only; adapters **not built** |
| Tauri shell / Windows installer | Built by CI (`.github/workflows/build.yml`); **not yet run** |

## Develop
```bash
npm install
npm run tauri dev      # Windows 10/11 x64, Rust + WebView2
npm run tauri build    # NSIS + MSI in target/release/bundle
```
Core logic is in `crates/pi-core` (testable anywhere); Tauri commands in `src-tauri/src/lib.rs`.

## Data integrity
No employee counts, executives or contacts are invented. OSM supplies name, location, phone, website only. Contacts and headcount are manual or from a licensed provider you configure.
