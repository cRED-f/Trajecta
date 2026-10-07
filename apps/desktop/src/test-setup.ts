// Runs before each test file. Node exposes a `localStorage` global that is
// `undefined` unless `--localstorage-file` is passed, which makes zustand's
// persist middleware throw on every `set`. Stub it with memory storage.
const memory = new Map<string, string>();

const storage: Storage = {
  getItem: (key) => memory.get(key) ?? null,
  setItem: (key, value) => {
    memory.set(key, String(value));
  },
  removeItem: (key) => {
    memory.delete(key);
  },
  clear: () => {
    memory.clear();
  },
  key: (index) => [...memory.keys()][index] ?? null,
  get length() {
    return memory.size;
  },
};

if (typeof localStorage === "undefined" || !localStorage) {
  Object.defineProperty(globalThis, "localStorage", {
    value: storage,
    configurable: true,
    writable: true,
  });
}
