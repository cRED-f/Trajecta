#!/usr/bin/env node
/** Opt-in, non-destructive plugin configuration for Trajecta's managed Bifrost.
 *
 * Does not touch config.db, credentials, provider routing, or virtual keys.
 * Config-store overrides may take precedence: verify the *live* dashboard.
 */
import { randomBytes } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

const args = process.argv.slice(2);
const apply = args.includes('--apply');
const configArgument = args.find(a => a.startsWith('--config='));
if (args.some(a => a !== '--apply' && !a.startsWith('--config='))) {
  throw new Error('Usage: node scripts/configure-bifrost-phoenix.mjs [--apply] [--config=PATH]');
}
const local = process.env.LOCALAPPDATA;
if (!configArgument && !local) {
  throw new Error('LOCALAPPDATA is required without an explicit --config path (Windows).');
}
const file = configArgument
  ? resolve(configArgument.slice('--config='.length))
  : join(local, 'ai.trajecta.desktop', 'data', 'bifrost', 'config.json');
if (!existsSync(file)) throw new Error(`Managed Bifrost config missing: ${file}`);
const current = JSON.parse(readFileSync(file, 'utf8'));
if (!current || typeof current !== 'object' || Array.isArray(current)) {
  throw new Error('Invalid Bifrost configuration');
}
if (current.plugins != null && !Array.isArray(current.plugins)) {
  throw new Error('Unsupported Bifrost plugins layout: use its dashboard instead');
}
const plugins = [...(current.plugins ?? [])];
const existing = plugins.find(p => p?.name === 'otel');
const desired = {
  name: 'otel',
  enabled: true,
  config: {
    service_name: 'trajecta-bifrost',
    collector_url: 'http://127.0.0.1:6006/v1/traces',
    trace_type: 'open_inference',
    protocol: 'http',
    metrics_enabled: false,
  },
};
if (existing && JSON.stringify(existing) !== JSON.stringify(desired)) {
  throw new Error('Existing OTEL plugin has user configuration. Configure in Bifrost dashboard instead; not overwriting.');
}
if (!existing) plugins.push(desired);
current.plugins = plugins;
const updated = JSON.stringify(current, null, 2) + '\n';
if (apply && readFileSync(file, 'utf8') !== updated) {
  mkdirSync(join(file, '..'), { recursive: true });
  const temporary = `${file}.${randomBytes(6).toString('hex')}.tmp`;
  try {
    writeFileSync(temporary, updated, { flag: 'wx', mode: 0o600 });
    renameSync(temporary, file);
  } finally {
    if (existsSync(temporary)) rmSync(temporary, { force: true });
  }
}
console.log(`${apply ? 'Wrote' : 'Preview only (add --apply to write)'}: ${file}`);
console.log('Bifrost may load plugin settings from config.db. Restart Bifrost and verify the live OTEL plugin in http://127.0.0.1:8080.');
console.log('OTLP/HTTP collector: http://127.0.0.1:6006/v1/traces');
