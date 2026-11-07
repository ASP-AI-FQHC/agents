# ASP Proposal Builder — macOS desktop app

A native Mac app wrapper (Electron) around the ASP Proposal Builder. Same tool as the web
version, but it runs as a real app — its own icon and window, fully offline, and **Download Word /
JSON open a native macOS Save dialog** (no browser, no sandbox, no download quirks).

Everything stays on the Mac; nothing is uploaded.

## Build the installer (.dmg) — must be done on a Mac

macOS apps can only be built on macOS, so this last step happens on a Mac (yours, a colleague's,
or IT's). It's a one-time build; afterward you have a `.dmg` anyone can install.

1. **Install Node.js** (one time): https://nodejs.org → download the **LTS** installer → run it.
2. **Double-click `build-mac.command`** in this folder.
   - It runs `npm install` (first run downloads Electron — a few minutes) then builds the app.
   - When it finishes, the **`dist/`** folder opens with **`ASP Proposal Builder-1.0.0.dmg`** (or
     similar) inside.
3. **Install:** open the `.dmg`, drag **ASP Proposal Builder** to Applications. Done.

> If macOS says the app "cannot be opened because it is from an unidentified developer,"
> right-click the app → **Open** → **Open** (only needed the first time). To remove that prompt
> entirely, code-sign the build with your organization's Apple Developer account (see below).

### Prefer the command line?

```bash
cd desktop
npm install
npm run dist      # -> dist/*.dmg
# or run without building an installer:
npm start
```

## Updating the proposal template / tool

The app's UI and the Word template are bundled in `renderer/index.html` (the same self-contained
file as the web build). To pick up a new version, replace `renderer/index.html` and rebuild.

## Files

| File | Purpose |
|------|---------|
| `main.js` | Electron main process — creates the window, handles the native Save dialog. |
| `preload.js` | Safe bridge exposing `window.desktop.save(...)` to the page. |
| `renderer/index.html` | The proposal builder UI (template + logo + zip engine embedded). |
| `build/icon.png` | App icon (electron-builder generates the `.icns` at build time). |
| `build-mac.command` | Double-click to build the `.dmg` on a Mac. |
| `package.json` | Electron + electron-builder config (targets macOS `.dmg`). |

## Optional: code signing (no Gatekeeper warning)

With an Apple Developer account, set these before building and electron-builder signs + notarizes:

```bash
export CSC_LINK=/path/to/DeveloperIDApplication.p12
export CSC_KEY_PASSWORD=********
export APPLE_ID=you@company.com
export APPLE_APP_SPECIFIC_PASSWORD=****-****-****-****
export APPLE_TEAM_ID=XXXXXXXXXX
npm run dist
```
