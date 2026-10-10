# Native Windows execution sandbox

Trajecta's `execute` tool uses a Windows **AppContainer** for reduced filesystem
and network privileges, plus a Windows **Job Object** to limit RAM, CPU share,
process-tree lifetime and wall-clock runtime. There is no Docker dependency.

## Build & install

In an **x64 Native Tools Command Prompt for Visual Studio**, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-native-sandbox.ps1
```

`node scripts/install-local.mjs` builds the native helper and installs it beside
`Trajecta.exe` in `%LOCALAPPDATA%\ai.trajecta.desktop\runtime`.
If this stage fails, installation aborts rather than silently enabling host
execution. Settings → Sandbox Execution shows operational status.

The backend provisions a unique AppContainer SID for the selected workspace.
It grants MODIFY to that SID for the chosen workspace and READ to uploads,
never `Everyone` or `ALL APPLICATION PACKAGES`. On normal shutdown, these
specific access-control entries are revoked. Stale ACEs after abnormal shutdown
are profile-specific; remove manually with `icacls` if necessary.

## Current restrictions & unverified elements

**Security validation must be completed on actual supported Windows machines
before calling the implementation production-safe.** The Linux development
container cannot execute Windows security APIs or run the Windows helper.

- The adapter grants READ to the Python interpreter installations (including
  Trajecta venv) and exposes an allowlisted PATH for Python, Git, Node and npm.
  User-profile-installed Node/Git toolchains may still be unreadable by AppContainer;
  those commands fail rather than silently running unconfined.
- The current helper launches Windows PowerShell (`-NoProfile`), not a POSIX shell.
  Deep Agents builtin file commands should target routed `/workspace/` file tools.
- Network is disabled by default. Users can explicitly enable outbound internet
  through Settings; this grants `internetClient` to the AppContainer. Test both
  modes on Windows before relying on enforcement.
- There is **no disk I/O quota**. RAM, CPU and timeout are enforced by Job Objects.
- The separate `process_start` host tool is NOT sandboxed. It still uses HITL
  permissions and should not be described as isolated.
- AppContainer access is at the folder level. Existing links/junctions are
  rejected before ACL provisioning; symlink creation after ACL setup and other
  race attacks require additional Windows penetration tests.
- Native subprocesses inherit a minimal allowlisted environment, not Trajecta's
  provider credentials. The helper must not automatically inherit PATH secrets.

## Windows acceptance tests (required)

1. Compile the helper with MSVC and run `powershell -File scripts/test-native-sandbox.ps1`; verify SID is an AppContainer SID.
2. In a selected project run `Write-Output 'hello'` and write a file.
3. Try to read a private file elsewhere in the user profile (must be denied).
4. Try to contact both a public URL and loopback service (must be denied).
5. Spawn descendants, test memory exhaustion, high CPU, and timeout; verify all
   descendants are killed and resources capped.
6. Configure terminal to `DENY`; ensure `execute` vanishes, not just returns errors.
7. Re-select another workspace and verify first workspace isn't exposed.
8. Verify native helper missing, restricted token creation failing, and ACL
   grant failing are all fail-closed; verify no direct-host fallback.
9. Test Windows 10, Windows 11, standard user and Tauri installed runtime.

Passing static code review or Linux unit tests is insufficient to attest to
Windows sandbox isolation.
