import test from 'node:test';
import assert from 'node:assert/strict';
import { packageManagerCommand } from '../windows-package-manager.mjs';

test('pnpm flags are passed unquoted to the Windows shim', () => {
  assert.deepEqual(packageManagerCommand('pnpm', ['--version'], { pnpmEntrypoint: '' }), {
    program: 'cmd.exe', args: ['/d', '/c', 'pnpm --version'],
  });
  assert.deepEqual(packageManagerCommand('pnpm', ['tauri', 'build', '--no-bundle'], { pnpmEntrypoint: '' }).args[2],
    'pnpm tauri build --no-bundle');
  assert.deepEqual(packageManagerCommand('pnpm', ['install', '--frozen-lockfile'], { pnpmEntrypoint: '' }).args[2],
    'pnpm install --frozen-lockfile');
});

test('uses direct Node pnpm entrypoint where available, including paths with spaces', () => {
  assert.deepEqual(packageManagerCommand('pnpm', ['--version'], {
    pnpmEntrypoint: 'C:\\Program Files\\nodejs\\node_modules\\corepack\\dist\\pnpm.js',
    nodePath: 'C:\\Program Files\\nodejs\\node.exe', fileExists: () => true,
  }), {
    program: 'C:\\Program Files\\nodejs\\node.exe',
    args: ['C:\\Program Files\\nodejs\\node_modules\\corepack\\dist\\pnpm.js', '--version'],
  });
});

test('npm link works and command injection is rejected', () => {
  assert.deepEqual(packageManagerCommand('npm', ['link'], { pnpmEntrypoint: '' }).args[2], 'npm link');
  assert.throws(() => packageManagerCommand('pnpm', ['--version & del C:\\data'], { pnpmEntrypoint: '' }), /Unsafe/);
});

test('does not accidentally execute npm when npm_execpath is not pnpm', () => {
  assert.equal(packageManagerCommand('pnpm', ['--version'], {
    pnpmEntrypoint: 'C:\\node_modules\\npm\\bin\\npm-cli.js', fileExists: () => true,
  }).program, 'cmd.exe');
});
