#!/usr/bin/env node
// Run by Settings after the app begins quitting. Leaves production data untouched.
import { execFileSync, spawn, spawnSync } from 'node:child_process';
import { appendFileSync, existsSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { homedir } from 'node:os';
import { fileURLToPath } from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const base=join(process.env.LOCALAPPDATA || join(homedir(),'AppData','Local'),'ai.trajecta.desktop');
const log=join(base,'update.log');
const pid=Number(process.argv[2]);
function info(line) { appendFileSync(log,`${new Date().toISOString()} ${line}\n`); }
async function waitForExit() {
  if (!Number.isInteger(pid)||pid<1) throw new Error('Missing application PID');
  for(let n=0;n<150;n++) {
    try { process.kill(pid,0); } catch(e) { if(e.code==='ESRCH') return; }
    await new Promise(r=>setTimeout(r,200));
  }
  throw new Error('Trajecta did not exit; update cancelled');
}
function command(program,args,cwd=root) {
  info(`Running ${program} ${args.join(' ')}`);
  // Do not use shell expansion for Git arguments.
  return execFileSync(program,args,{cwd,encoding:'utf8',windowsHide:true});
}
try {
  await waitForExit();
  if (command('git',['status','--porcelain']).trim()) throw new Error('Source clone has uncommitted changes');
  command('git',['pull','--ff-only']);
  const build = spawnSync(process.execPath,[join(root,'scripts','install-local.mjs')],{
    cwd:root,stdio:'ignore',windowsHide:true,timeout:30*60*1000,
  });
  if(build.error || build.status !== 0) throw new Error(`Installation failed: ${build.error?.message || build.status}`);
  info('Update installed successfully');
  const exe=join(base,'runtime','Trajecta.exe');
  if(existsSync(exe)) spawn(exe,[],{detached:true,stdio:'ignore',windowsHide:true}).unref();
} catch(err) { info(`Update failed: ${err.stack||err}`); process.exitCode=1; }
