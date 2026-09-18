# Desktop App Preview

## How to reproduce artifacts

1. `cd apps/desktop && pnpm install --frozen-lockfile` (installs node_modules)
2. Copy `.env.local` from the main checkout if missing (contains `VITE_TRAJECTA_API_URL`).

## How to run the server

```bash
cd apps/desktop && pnpm run dev
```

- Vite dev server on **http://127.0.0.1:1420**
- Requires the Python backend running on port 8420 for full functionality
- On Windows, for detached mode: `nohup pnpm run dev > /dev/null 2>&1 &` in Git Bash

## Window icon

The Tauri window icon is set programmatically in `src-tauri/src/lib.rs` via a `setup` hook that calls `window.set_icon()`. The source SVG is `public/icon.svg`; regenerate platform icons with `npx tauri icon public/icon.svg`.
