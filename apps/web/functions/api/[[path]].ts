/**
 * Cloudflare Pages Function — transparent proxy for all /api/* requests.
 *
 * Routes every /api/... call to the Fly.io backend so the PWA can use
 * relative URLs and avoid CORS issues entirely.
 */

const UPSTREAM = "https://ai-plot-twist.fly.dev";

export async function onRequest(
  context: EventContext<Record<string, unknown>, string, unknown>,
): Promise<Response> {
  const url = new URL(context.request.url);
  const target = `${UPSTREAM}${url.pathname}${url.search}`;

  return fetch(target, {
    method: context.request.method,
    headers: context.request.headers,
    body: context.request.body,
    redirect: "follow",
  });
}
