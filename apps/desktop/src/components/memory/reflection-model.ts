/** The persisted reflection model is a qualified Bifrost id: provider/model. */
export function providerForReflectionModel(value: string | null | undefined): string {
  const model = (value ?? "").trim();
  const slash = model.indexOf("/");
  return slash > 0 ? model.slice(0, slash) : "";
}

/** Bifrost returns qualified ids but some catalogs may contain bare names. */
export function qualifyReflectionModel(provider: string, name: string): string {
  const value = name.trim();
  return !provider || !value || value.startsWith(`${provider}/`)
    ? value
    : `${provider}/${value}`;
}
