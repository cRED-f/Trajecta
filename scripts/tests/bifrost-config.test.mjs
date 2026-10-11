import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { prepareManagedBifrostIdentity } from '../bifrost-config.mjs';

function fixture(callback) {
  const dir = mkdtempSync(join(tmpdir(), 'trajecta-bifrost-config-'));
  try { callback(dir); } finally { rmSync(dir, { recursive: true, force: true }); }
}

test('seeds stable managed identity and sqlite-backed governance', () => fixture((dir) => {
  const result = prepareManagedBifrostIdentity(dir);
  const key = readFileSync(result.keyPath, 'utf8').trim();
  const conf = JSON.parse(readFileSync(result.configPath, 'utf8'));
  assert.match(key, /^sk-bf-[a-f0-9]{64}$/);
  assert.equal(conf.config_store.enabled, true);
  assert.deepEqual(conf.governance.virtual_keys.map((vk) => vk.value), ['env.BIFROST_VIRTUAL_KEY']);
  prepareManagedBifrostIdentity(dir);
  assert.equal(readFileSync(result.keyPath, 'utf8').trim(), key);
}));

test('preserves dashboard providers, routing, and unrelated virtual keys on upgrade', () => fixture((dir) => {
  const existing = {
    providers: { '9router': { keys: [{ name: 'user-owned-key' }] } },
    routing: { preferred: '9router' },
    governance: { virtual_keys: [{ id: 'vk-work', name: 'keep-me', provider_configs: [] }] },
  };
  writeFileSync(join(dir, 'config.json'), JSON.stringify(existing));
  prepareManagedBifrostIdentity(dir);
  const after = JSON.parse(readFileSync(join(dir, 'config.json')));
  assert.deepEqual(after.providers, existing.providers);
  assert.deepEqual(after.routing, existing.routing);
  assert.deepEqual(after.governance.virtual_keys[0], existing.governance.virtual_keys[0]);
  assert.equal(after.governance.virtual_keys[1].allow_all_providers, true);
}));

test('does not duplicate managed key on reinstallation', () => fixture((dir) => {
  prepareManagedBifrostIdentity(dir);
  prepareManagedBifrostIdentity(dir);
  const conf = JSON.parse(readFileSync(join(dir, 'config.json')));
  assert.equal(conf.governance.virtual_keys.filter((v) => v.id === 'vk-trajecta-local').length, 1);
}));

test('rejects malformed gateway config without modifying it', () => fixture((dir) => {
  writeFileSync(join(dir, 'config.json'), '{invalid json');
  assert.throws(() => prepareManagedBifrostIdentity(dir), SyntaxError);
  assert.equal(readFileSync(join(dir, 'config.json'), 'utf8'), '{invalid json');
}));
