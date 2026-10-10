// Resolve npm/pnpm execution on Windows without quoting CLI switches as positional arguments.
// The installer only passes constant command arguments; reject shell metacharacters in fallback.
import { existsSync } from 'node:fs';

export function packageManagerCommand(program, args, options = {}) {
  if (program !== 'pnpm' && program !== 'npm') {
    throw new Error(`Unsupported package manager: ${program}`);
  }

  const pnpmEntrypoint = options.pnpmEntrypoint ?? process.env.npm_execpath;
  const nodePath = options.nodePath ?? process.execPath;
  const fileExists = options.fileExists ?? existsSync;
  if (program === 'pnpm' && pnpmEntrypoint && /pnpm/i.test(pnpmEntrypoint)
      && /\.(?:cjs|mjs|js)$/i.test(pnpmEntrypoint) && fileExists(pnpmEntrypoint)) {
    // Avoid cmd.exe entirely when the active pnpm script can be invoked with Node.
    return { program: nodePath, args: [pnpmEntrypoint, ...args] };
  }

  // `pnpm "--version"` is NOT equivalent to `pnpm --version` for all pnpm versions.
  // Safe literal arguments remain unquoted; command paths are handled separately by spawnSync.
  for (const arg of args) {
    if (!/^[a-zA-Z0-9_./:@=+\\-]+$/.test(arg)) {
      throw new Error(`Unsafe package manager argument: ${arg}`);
    }
  }
  return { program: 'cmd.exe', args: ['/d', '/c', [program, ...args].join(' ')] };
}
