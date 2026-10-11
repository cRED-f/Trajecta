/** Link to the management UI on the configured Bifrost gateway. */
export function bifrostDashboardUrl(baseUrl: string | null | undefined): string | null {
  if (!baseUrl) return null;
  try {
    const url = new URL(baseUrl);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    url.pathname = "/";
    url.search = "";
    url.hash = "";
    return url.toString();
  } catch {
    return null;
  }
}
