# Trajecta source installation — Windows (experimental)

Install directly from a Git clone; no npm publication, Docker, MSI, or GitHub release required.
This workflow builds a **native executable locally**. Windows 10/11 with WebView2,
Node.js 22+, pnpm, Git, Rust + MSVC C++ build tools, uv, and internet access for
first-time dependency downloads are required. `uv` provisions Python 3.11 for the backend.

## First installation

```powershell
git clone https://github.com/<OWNER>/<REPO>.git
cd <REPO>
pnpm trajecta:install
trajecta
```

If dependencies were not installed yet, `pnpm trajecta:install` installs the
frontend dependencies, builds the Rust/Tauri application with `--no-bundle`,
creates a managed Python virtual environment, and uses `npm link` to register
**one** global command: `trajecta`.

The Git clone must remain in place: Settings → Desktop & Application uses it for
checking and building updates. To update, commit/stash unfinished changes in
this clone first; updates use `git pull --ff-only` and do not overwrite a dirty
working tree. On update failure, review `%LOCALAPPDATA%\\ai.trajecta.desktop\\update.log`.

## Daily operation

- `trajecta` opens Trajecta, or focuses the existing window when already running.
- Closing the window hides it in the notification area when **Close to tray** is on.
- Use the tray menu's **Quit Trajecta** to exit the app and managed services.
- Startup, tray behavior, update checking, backend restart and uninstall are in
  **Settings → Desktop & Application**.
- `trajecta` is a Node.js launcher for a locally compiled Tauri executable,
  not `pnpm tauri dev`. No terminal needs to remain open.

## Storage and services

```
%LOCALAPPDATA%\\ai.trajecta.desktop\\
  runtime\\Trajecta.exe
  runtime\\venv\\          # managed Python + installed FastAPI package
  runtime\\backend\\config\\
  data\\.trajecta\\       # persistent SQLite, Qdrant, skills, uploads, config
  logs\\                 # backend and optional Bifrost logs
  desktop.json           # tray and startup preferences
  install.json           # original Git-clone location
```

The installed FastAPI backend runs at `127.0.0.1:8420`. If that port is occupied,
Trajecta **fails closed** rather than attaching to an unknown backend. Data is
separated from build artifacts so updates don't delete conversations or memories.
The built-in embedded Qdrant mode needs no Docker service.

**Bifrost:** Trajecta can launch a local gateway executable placed at
`runtime\\bifrost\\bifrost.exe` (if compatible with the project's Bifrost
configuration). Otherwise it connects to a separately running Bifrost gateway
at its configured endpoint. **Bifrost is not downloaded or built automatically**
by this patch; Ollama and 9Router also remain externally managed. Confirm they
are reachable in Settings before relying on chat. Docker-dependent sandbox
features still require Docker if enabled; native desktop use itself does not.

**Existing data:** The installer does not overwrite or silently migrate your
old checkout's `.trajecta` folder. Close the old backend and make a consistent
backup before copying historical data to the production data directory.
SQLite WAL files and embedded Qdrant files must be captured while quiescent.

## Development and testing

Development uses a separate Tauri application identifier and service port:

```powershell
# Terminal A, repository root
$env:TRAJECTA_SERVER_PORT = "8421"
python -m server.src.main

# Terminal B
cd apps/desktop
pnpm dev:desktop
```

The development UI uses `http://127.0.0.1:8421/api/v1` through
`.env.development`. Developer resources/data are separate from installed app
resources. **Do not connect test runs to the production data directory.**

## Maintenance and recovery

- **Check for updates:** compares the checked-out commit with the matching
  branch on `origin`. Differences do not prove the remote is ahead; review the
  branch history before installing. The updater refuses uncommitted changes.
- **Update & restart:** closes the app, fast-forwards the clone, rebuilds the
  runtime, and reopens Trajecta. A previous binary/venv is kept in `backups/`.
- **Uninstall:** unregisters autostart and global npm link and removes runtime;
  **saved data and logs are retained** by default.
- **New Rust dependency:** run the first build and commit the generated
  `apps/desktop/src-tauri/Cargo.lock` change to the repository. The supplied
  patch cannot regenerate the Windows Rust dependency lockfile in this
  environment.

**Current verification limit:** The source patch has syntax/static checks and
backend configuration tests; Windows/Tauri installation and full app integration
must be tested on a Windows machine with Node/pnpm/Rust/MSVC dependencies.
**Security:** The locally bound FastAPI API inherits existing Trajecta API
permissions; this patch does not add per-session loopback authentication.
Do not expose port 8420 outside localhost or treat this as a hardened multi-user
service without additional authentication and threat-model testing.
