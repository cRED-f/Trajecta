#!/usr/bin/env node
// Uninstall runtime and CLI only; preserve production memories/config/logs.
import { execFileSync, spawnSync } from 'node:child_process';
import { appendFileSync, rmSync, existsSync } from 'node:fs';
import { dirname, resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { homedir } from 'node:os';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const base=join(process.env.LOCALAPPDATA||join(homedir(),'AppData','Local'),'ai.trajecta.desktop');
const log=join(base,'uninstall.log');
const pid=Number(process.argv[2]);
const info=(text)=>appendFileSync(log,`${new Date().toISOString()} ${text}\n`);
try {
  if(!Number.isInteger(pid)||pid<1) throw new Error('Missing application PID');
  let stopped=false;
  for(let n=0;n<150;n++) {
    try { process.kill(pid,0); } catch(e) { if(e.code==='ESRCH') {stopped=true;break;} }
    await new Promise(r=>setTimeout(r,200));
  }
  if(!stopped) throw new Error('App still running; refusing to uninstall');
  try {execFileSync('reg',['delete','HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run','/v','Trajecta','/f'],{windowsHide:true});}catch{}
  try {
    const cli = spawnSync('cmd.exe',['/d','/s','/c','npm unlink -g trajecta'],{cwd:root,windowsHide:true});
    if(cli.status !== 0) info(`CLI unlink exit code: ${cli.status}`);
  } catch(e) {info(`CLI unlink warning: ${e.message}`)}
  if(existsSync(join(base,'runtime'))) rmSync(join(base,'runtime'),{recursive:true,force:true});
  rmSync(join(base,'install.json'),{force:true});
  info('Uninstalled runtime. Production data, logs and settings preserved.');
} catch(err) {info(`Uninstall failed: ${err.stack||err}`);process.exitCode=1;}
