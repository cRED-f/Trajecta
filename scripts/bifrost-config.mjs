// Own only Trajecta's managed virtual key. Provider credentials and routing
// remain dashboard-owned and must never be overwritten during installation.
import { randomBytes } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const OWNED_KEY_ID = 'vk-trajecta-local';

function atomicWrite(path, content, options = {}) {
  const temporary = `${path}.${randomBytes(6).toString('hex')}.tmp`;
  try {
    writeFileSync(temporary, content, { flag: 'wx', ...options });
    renameSync(temporary, path);
  } finally {
    if (existsSync(temporary)) rmSync(temporary, { force: true });
  }
}

export function prepareManagedBifrostIdentity(directory) {
  mkdirSync(directory, { recursive: true });
  const keyPath = join(directory, 'virtual-key');
  // Do not rotate an existing identity; Bifrost uses it for persisted access.
  if (!existsSync(keyPath)) {
    try {
      writeFileSync(keyPath, `sk-bf-${randomBytes(32).toString('hex')}\n`, {
        flag: 'wx', mode: 0o600,
      });
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
    }
  }
  const key = readFileSync(keyPath, 'utf8').trim();
  if (!/^sk-bf-[A-Za-z0-9_-]{16,}$/.test(key)) {
    throw new Error(`Invalid managed Bifrost virtual key in ${keyPath}`);
  }

  const configPath = join(directory, 'config.json');
  // On upgrades Bifrost may already have a config file. Preserve every provider,
  // routing policy, admin setting and unrelated key exactly as it was.
  const config = existsSync(configPath)
    ? JSON.parse(readFileSync(configPath, 'utf8'))
    : {
        $schema: 'https://www.getbifrost.ai/schema',
        config_store: { enabled: true, type: 'sqlite', config: { path: './config.db' } },
      };
  if (!config || typeof config !== 'object' || Array.isArray(config)) {
    throw new Error('Bifrost config.json must be an object');
  }
  if (config.config_store?.enabled === false) {
    throw new Error('Managed Bifrost requires config_store.enabled=true for dashboard configuration');
  }
  if (config.governance != null && (typeof config.governance !== 'object' || Array.isArray(config.governance))) {
    throw new Error('Bifrost governance configuration must be an object');
  }
  config.governance ??= {};
  if (config.governance.virtual_keys != null && !Array.isArray(config.governance.virtual_keys)) {
    throw new Error('Bifrost governance.virtual_keys must be an array');
  }
  config.governance.virtual_keys ??= [];
  const existing = config.governance.virtual_keys.findIndex(
    (entry) => entry?.id === OWNED_KEY_ID,
  );
  const managedKey = {
    ...(existing >= 0 ? config.governance.virtual_keys[existing] : {}),
    id: OWNED_KEY_ID,
    name: 'trajecta-local',
    value: 'env.BIFROST_VIRTUAL_KEY',
    is_active: true,
    allow_all_providers: true,
  };
  if (existing < 0) config.governance.virtual_keys.push(managedKey);
  else config.governance.virtual_keys[existing] = managedKey;
  const desired = JSON.stringify(config, null, 2) + '\n';
  if (!existsSync(configPath) || readFileSync(configPath, 'utf8') !== desired) {
    atomicWrite(configPath, desired, { mode: 0o600 });
  }
  return { keyPath, configPath };
}
