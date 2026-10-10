import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));
const read = (relative) => readFileSync(new URL(relative, `file://${root}/`), 'utf8');

const frontend = JSON.parse(read('apps/desktop/package.json'));
const rustManifest = read('apps/desktop/src-tauri/Cargo.toml');
const rustLock = read('apps/desktop/src-tauri/Cargo.lock');
const pnpmLock = read('apps/desktop/pnpm-lock.yaml');

for (const [name, version] of [['dialog', '2.7.3'], ['opener', '2.5.5']]) {
  test(`Tauri ${name} JS and Rust plugins are both pinned to ${version}`, () => {
    assert.equal(frontend.dependencies[`@tauri-apps/plugin-${name}`], version);
    assert.match(rustManifest, new RegExp(`^tauri-plugin-${name} = "=${version.replaceAll('.', '\\.')}"$`, 'm'));
    assert.match(rustLock, new RegExp(`name = "tauri-plugin-${name}"\\nversion = "${version.replaceAll('.', '\\.')}"`));
    assert.match(pnpmLock, new RegExp(`'@tauri-apps/plugin-${name}@${version.replaceAll('.', '\\.')}':`));
    assert.match(pnpmLock, new RegExp(`'@tauri-apps/plugin-${name}':\\n        specifier: ${version.replaceAll('.', '\\.')}\\n        version: ${version.replaceAll('.', '\\.')}`));
  });
}

test('pnpm dialog snapshot resolves against the same API version as the root', () => {
  const api = pnpmLock.match(/'@tauri-apps\/api':\n        specifier: \^2\n        version: ([0-9.]+)/)?.[1];
  assert.ok(api, 'expected a pinned JS API dependency');
  assert.match(pnpmLock, new RegExp(`'@tauri-apps/plugin-dialog@2\\.7\\.3':\\n    dependencies:\\n      '@tauri-apps/api': ${api.replaceAll('.', '\\.')}`));
  assert.doesNotMatch(pnpmLock, /'@tauri-apps\/plugin-dialog@2\.8\.1'/);
});
