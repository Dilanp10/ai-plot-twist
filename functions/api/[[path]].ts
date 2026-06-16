// Cloudflare Pages Function: proxy /api/* to the Fly.io backend.
// Pages Functions live under functions/ at the project root.
// This catch-all handler forwards every method (GET, POST, etc.) and
// preserves headers, body, and the splat path.

const BACKEND_ORIGIN = "https://ai-plot-twist.fly.dev";

export const onRequest: PagesFunction = async (context) => {
  const url = new URL(context.request.url);
  const targetUrl = `${BACKEND_ORIGIN}${url.pathname}${url.search}`;

  const init: RequestInit = {
    method: context.request.method,
    headers: context.request.headers,
    body:
      context.request.method === "GET" || context.request.method === "HEAD"
        ? undefined
        : await context.request.arrayBuffer(),
    redirect: "manual",
  };

  return fetch(targetUrl, init);
};
