import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { BIFROST_NPM_VERSION, BIFROST_TRANSPORT_VERSION, BIFROST_NPM_INSTALL_ARGS, prepareBifrostStaging, readBifrostExecutable, writeBifrostManifest } from '../bifrost-install.mjs';

function fakePackage(bin = { bifrost: 'bin/cli.js' }, version = BIFROST_NPM_VERSION) {
  const root = mkdtempSync(join(tmpdir(), 'trajecta-bifrost-test-'));
  const pkg = join(root, 'node_modules', '@maximhq', 'bifrost');
  mkdirSync(join(pkg, 'bin'), { recursive: true });
  writeFileSync(join(pkg, 'package.json'), JSON.stringify({ name: '@maximhq/bifrost', version, bin }));
  writeFileSync(join(pkg, 'bin', 'cli.js'), '#!/usr/bin/env node\n');
  return { root, cleanup: () => rmSync(root, { recursive: true, force: true }) };
}

test('validates and records pinned Bifrost launcher', () => {
  const fixture = fakePackage();
  try {
    assert.equal(readBifrostExecutable(fixture.root), 'node_modules/@maximhq/bifrost/bin/cli.js');
    const actual = writeBifrostManifest(fixture.root, '/fake/node.exe');
    assert.equal(actual.transport_version, BIFROST_TRANSPORT_VERSION);
    assert.equal(actual.node_executable, '/fake/node.exe');
    assert.deepEqual(JSON.parse(readFileSync(join(fixture.root, 'launcher.json'), 'utf8')), actual);
  } finally { fixture.cleanup(); }
});

test('refuses unexpected package versions', () => {
  const fixture = fakePackage({ bifrost: 'bin/cli.js' }, '0.0.0');
  try { assert.throws(() => readBifrostExecutable(fixture.root), /Unexpected Bifrost package/); }
  finally { fixture.cleanup(); }
});

test('refuses launcher paths outside installed package', () => {
  const fixture = fakePackage({ bifrost: '../../outside.js' });
  try { assert.throws(() => readBifrostExecutable(fixture.root), /not found inside/); }
  finally { fixture.cleanup(); }
});

test('rejects missing package CLI', () => {
  const fixture = fakePackage({ wrong: 'bin/cli.js' });
  try { assert.throws(() => readBifrostExecutable(fixture.root), /no bifrost executable/); }
  finally { fixture.cleanup(); }
});


test('creates a private npm project that anchors Bifrost installation to staging', () => {
  const root = mkdtempSync(join(tmpdir(), 'trajecta-bifrost-staging-'));
  try {
    prepareBifrostStaging(root);
    const manifest = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
    assert.equal(manifest.name, 'trajecta-managed-bifrost');
    assert.equal(manifest.private, true);
    assert.equal(manifest.dependencies, undefined);
    assert.ok(BIFROST_NPM_INSTALL_ARGS.includes('--prefix=.'));
    assert.ok(BIFROST_NPM_INSTALL_ARGS.includes('--workspaces=false'));
    assert.ok(BIFROST_NPM_INSTALL_ARGS.includes(`@maximhq/bifrost@${BIFROST_NPM_VERSION}`));
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('refuses manifest creation if npm installed into the wrong project', () => {
  const root = mkdtempSync(join(tmpdir(), 'trajecta-bifrost-empty-'));
  try {
    prepareBifrostStaging(root);
    assert.throws(() => writeBifrostManifest(root), /npm did not install into the isolated Bifrost directory/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
