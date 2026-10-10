#!/usr/bin/env node
// Windows-first source installer. Does not publish an npm package or move user data.
import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, copyFileSync, writeFileSync, renameSync, rmSync } from 'node:fs';
import { join, resolve, dirname } from 'node:path';
import { homedir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { packageManagerCommand } from './windows-package-manager.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
if (process.platform !== 'win32') throw new Error('Local source installation currently supports Windows only.');
const base = join(process.env.LOCALAPPDATA || join(homedir(),'AppData','Local'), 'ai.trajecta.desktop');
const runtime = join(base, 'runtime');
const data = join(base, 'data');
const backups = join(base, 'backups');
const venv = join(runtime, 'venv');
const stagingVenv = join(runtime, 'venv.new');
const python = join(stagingVenv, 'Scripts', 'python.exe');
const built = join(root, 'apps','desktop','src-tauri','target','release','trajecta-desktop.exe');
const target = join(runtime, 'Trajecta.exe');
const nativeSandbox = join(root, 'native', 'windows', 'bin', 'trajecta-native-sandbox.exe');
const installedSandbox = join(runtime, 'trajecta-native-sandbox.exe');

function call(program, args, cwd = root) {
  console.log(`> ${program} ${args.join(' ')}`);
  // Prefer pnpm's actual Node entrypoint; Windows cmd shims may mangle quoted flags.
  const command = program === 'pnpm' || program === 'npm'
    ? packageManagerCommand(program, args)
    : { program, args };
  const result = spawnSync(command.program, command.args, { cwd, stdio: 'inherit', windowsHide: true });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${program} failed with exit code ${result.status}`);
}
function requireCommand(cmd,args=['--version']) {
  try {
    if (cmd === 'pnpm') call('pnpm', args);
    else execFileSync(cmd,args,{stdio:'ignore', windowsHide:true});
  }
  catch (error) { throw new Error(`Cannot run ${cmd} ${args.join(' ')}: ${error.message}`); }
}
requireCommand('git'); requireCommand('node'); requireCommand('pnpm'); requireCommand('uv'); requireCommand('cargo');
// The native sandbox is mandatory for a full source installation. Fail rather
// than silently shipping a desktop without restricted execute support.
call('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', join(root, 'scripts', 'build-native-sandbox.ps1')]);
if (!existsSync(nativeSandbox)) throw new Error('Native sandbox helper was not built');
if (!existsSync(join(root,'.git'))) throw new Error('Install from a Git clone, not a downloaded source archive.');
mkdirSync(runtime,{recursive:true}); mkdirSync(data,{recursive:true}); mkdirSync(backups,{recursive:true});
// Keep an existing working installation in place until the build succeeds.
call('pnpm',['install','--frozen-lockfile'],join(root,'apps','desktop'));
call('pnpm',['tauri','build','--no-bundle'],join(root,'apps','desktop'));
if (!existsSync(built)) throw new Error(`Tauri did not create expected executable: ${built}`);
// Python packages live outside the checkout; no external container runtime needed daily.
if (existsSync(stagingVenv)) rmSync(stagingVenv,{recursive:true,force:true});
call('uv',['venv','--python','3.11',stagingVenv]);
call('uv',['pip','install','--python',python,root]);
// Reject a Python installation that resolves but cannot load the FastAPI entrypoint.
// Run from the same data directory as the installed desktop launcher.
// Check the optional LangGraph SQLite backend explicitly before replacing the active venv.
call(python,['-X','utf8','-c',
  'from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver; from langgraph.store.sqlite.aio import AsyncSqliteStore; import server.src.main',
],data);
mkdirSync(join(runtime,'backend','config'),{recursive:true});
copyFileSync(join(root,'config','default.yaml'),join(runtime,'backend','config','default.yaml'));
copyFileSync(join(root,'config','guardrail-rules.yaml'),join(runtime,'backend','config','guardrail-rules.yaml'));
// Commit only after both the Rust binary and Python dependencies have built.
// The updater waits for the app to exit before running this script.
const oldVenv = join(backups, 'venv.previous');
if (existsSync(oldVenv)) rmSync(oldVenv,{recursive:true,force:true});
if (existsSync(venv)) renameSync(venv,oldVenv);
renameSync(stagingVenv,venv);
if (existsSync(target)) copyFileSync(target,join(backups,'Trajecta.previous.exe'));
copyFileSync(built,target + '.new');
copyFileSync(nativeSandbox,installedSandbox + '.new');
try {
  if (existsSync(target)) rmSync(target);
  renameSync(target + '.new',target);
  if (existsSync(installedSandbox)) rmSync(installedSandbox);
  renameSync(installedSandbox + '.new',installedSandbox);
} catch (error) {
  if (!existsSync(target) && existsSync(join(backups,'Trajecta.previous.exe')))
    copyFileSync(join(backups,'Trajecta.previous.exe'),target);
  if (existsSync(venv)) rmSync(venv,{recursive:true,force:true});
  if (existsSync(oldVenv)) renameSync(oldVenv,venv);
  throw error;
}
const commit = execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim();
writeFileSync(join(base,'install.json'),JSON.stringify({source:root,commit,installed_at:new Date().toISOString()},null,2));
call('npm',['link'],root);
console.log('\nInstalled Trajecta. Run `trajecta` from any terminal. Settings handle maintenance.');
console.log(`Production data is isolated at: ${data}`);
