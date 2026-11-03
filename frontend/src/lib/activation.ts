export function consumeActivationUrl(href: string): { token: string | null; cleanUrl: string } {
  const url = new URL(href);
  const token = new URLSearchParams(url.hash.slice(1)).get("token");
  return {
    token,
    cleanUrl: `${url.pathname}${url.search}`,
  };
}
