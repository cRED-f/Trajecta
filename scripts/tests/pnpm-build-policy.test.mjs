import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const workspace = fileURLToPath(new URL('../../apps/desktop/pnpm-workspace.yaml', import.meta.url));

test('the desktop workspace explicitly approves esbuild without disabling supply-chain protections', () => {
  const yaml = readFileSync(workspace, 'utf8');
  assert.match(yaml, /^allowBuilds:\s*$/m);
  assert.match(yaml, /^\s+esbuild:\s+true\s*$/m);
  assert.doesNotMatch(yaml, /set this to true or false/i);
  assert.doesNotMatch(yaml, /^dangerouslyAllowAllBuilds:\s*true/m);
  assert.doesNotMatch(yaml, /^strictDepBuilds:\s*false/m);
});
