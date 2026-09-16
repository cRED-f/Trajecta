/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_TRAJECTA_API_URL: string | undefined;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}