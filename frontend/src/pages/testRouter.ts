/** The fetch stub the three Servers-tab suites share.
 *
 * One stub rather than three near-copies: the card, the tab and the library
 * map each drive the same routes from a different height, and three hand-rolled
 * routers drifted apart in exactly the places that matter -- whether a request
 * body was recorded at all, and whether a second body to one address could be
 * read back.
 *
 * The rules it keeps, which are the reason these suites are worth trusting:
 *
 *  - routing is by METHOD AND PATH, because a card reaches three routes at one
 *    address with different verbs (the catch-up trio) and two at one verb on
 *    different addresses (the two PUTs);
 *  - anything a test has not declared answers a 500 that names it, so a
 *    request the component should never have made shows up as a failure
 *    instead of as a passing test;
 *  - every call is recorded with its parsed body AND with whether a body was
 *    sent at all, because a bodyless DELETE is refused by the request
 *    validator before the handler runs, so "no body" and "{}" are different
 *    requests.
 */
import { act, fireEvent, screen } from "@testing-library/react";
import { vi } from "vitest";

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export interface Call {
  url: string;
  method: string;
  /** The parsed body, or undefined when the request carried none. */
  body: unknown;
  sentBody: boolean;
}

export type Route = (init: RequestInit | undefined) => Response;

/** Stub `fetch` with one handler per `"METHOD /path"`, and record every call. */
export function router(routes: Record<string, Route> = {}): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const sentBody = init?.body !== undefined && init?.body !== null;
      calls.push({
        url,
        method,
        body: sentBody ? JSON.parse(String(init?.body)) : undefined,
        sentBody,
      });
      const route = routes[`${method} ${url}`];
      return Promise.resolve(
        route === undefined
          ? json({ detail: `nothing declared ${method} ${url}` }, 500)
          : route(init),
      );
    }),
  );
  return calls;
}

/** Press the button with this accessible name, and let what it started settle. */
export async function click(name: string) {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name }));
  });
}

/** Every body sent to one route, in order. */
export function bodiesOf(calls: Call[], method: string, url: string): unknown[] {
  return calls
    .filter((call) => call.method === method && call.url === url)
    .map((call) => call.body);
}

/** The first body sent to one route. Throws when nothing reached it, which is
 * a clearer failure than an assertion against `undefined`. */
export function bodyOf(calls: Call[], method: string, url: string): unknown {
  const found = bodiesOf(calls, method, url);
  if (found.length === 0) throw new Error(`no ${method} ${url} was sent`);
  return found[0];
}

/** How many times one route was reached. */
export function countOf(calls: Call[], method: string, url: string): number {
  return bodiesOf(calls, method, url).length;
}
