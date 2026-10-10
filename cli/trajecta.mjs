#!/usr/bin/env node
import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { homedir } from 'node:os';

// One command only. Maintenance lives in Settings, not CLI flags.
if (process.argv.length > 2) {
  console.error('Run `trajecta` to open the desktop app. Manage updates and services in Settings.');
  process.exit(2);
}
if (process.platform !== 'win32') {
  console.error('The source installer currently supports Windows only.');
  process.exit(1);
}
const local = process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local');
const base = join(local, 'ai.trajecta.desktop');
const executable = join(base, 'runtime', 'Trajecta.exe');
if (!existsSync(executable)) {
  console.error('Trajecta is not installed. In your cloned repository run `pnpm trajecta:install`.');
  process.exit(1);
}
const child = spawn(executable, [], { detached: true, stdio: 'ignore', windowsHide: true });
child.on('error', (err) => { console.error(`Unable to launch Trajecta: ${err.message}`); process.exitCode = 1; });
child.unref();
