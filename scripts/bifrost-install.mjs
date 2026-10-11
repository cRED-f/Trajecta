// Manage the gateway's npm launcher independently of Trajecta's frontend lockfile.
// MaximHQ's launcher fetches the platform-specific gateway executable on first run.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { isAbsolute, join, relative, resolve } from 'node:path';

// Review the paired npm launcher and HTTP transport versions when upgrading.
export const BIFROST_NPM_VERSION = '1.6.2';
export const BIFROST_TRANSPORT_VERSION = 'v2.2.6';
export const BIFROST_PACKAGE = `@maximhq/bifrost@${BIFROST_NPM_VERSION}`;

// Never let npm resolve an ancestor workspace or a package-manager prefix
// inherited from `pnpm trajecta:install`. All dependency changes belong only
// to the disposable `runtime/bifrost.new` directory.
export const BIFROST_NPM_INSTALL_ARGS = [
  'install', '--prefix=.', '--workspaces=false', '--no-audit', '--no-fund',
  '--no-save', '--package-lock=false', BIFROST_PACKAGE,
];

export function prepareBifrostStaging(stagingDirectory) {
  mkdirSync(stagingDirectory, { recursive: true });
  // A package.json anchors npm's project selection to this folder. The
  // installer removes the staging folder before calling this function.
  writeFileSync(join(stagingDirectory, 'package.json'), JSON.stringify({
    name: 'trajecta-managed-bifrost', version: '1.0.0', private: true,
    description: 'Isolated installation of the Bifrost gateway launcher',
  }, null, 2) + '\n', { flag: 'wx' });
}

export function readBifrostExecutable(stagingDirectory) {
  const packageRoot = resolve(stagingDirectory, 'node_modules', '@maximhq', 'bifrost');
  const manifestPath = join(packageRoot, 'package.json');
  if (!existsSync(manifestPath)) {
    throw new Error(
      `Bifrost package missing at ${manifestPath}. ` +
      'npm did not install into the isolated Bifrost directory; ' +
      'check the install log and npm prefix/workspace configuration.'
    );
  }
  const pkg = JSON.parse(readFileSync(manifestPath, 'utf8'));
  if (pkg.name !== '@maximhq/bifrost' || pkg.version !== BIFROST_NPM_VERSION) {
    throw new Error(`Unexpected Bifrost package: ${pkg.name}@${pkg.version}`);
  }
  const bin = typeof pkg.bin === 'string' ? pkg.bin : pkg.bin?.bifrost;
  if (typeof bin !== 'string' || !bin.trim()) {
    throw new Error('Bifrost npm package has no bifrost executable entrypoint');
  }
  const script = resolve(packageRoot, bin);
  const nested = relative(packageRoot, script);
  if (nested.startsWith('..') || isAbsolute(nested) || !existsSync(script)) {
    throw new Error(`Bifrost npm launcher was not found inside the installed package: ${bin}`);
  }
  return relative(resolve(stagingDirectory), script).replace(/\\/g, '/');
}

export function writeBifrostManifest(stagingDirectory, nodeExe = process.execPath) {
  const entry = readBifrostExecutable(stagingDirectory);
  const manifest = {
    package: BIFROST_PACKAGE,
    transport_version: BIFROST_TRANSPORT_VERSION,
    node_executable: nodeExe,
    launcher: entry,
  };
  writeFileSync(join(stagingDirectory, 'launcher.json'), JSON.stringify(manifest, null, 2) + '\n');
  return manifest;
}
