import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { ActionCenter } from "./ActionCenter";

const FILTERS = {
  libraries: ["Movies", "Shows"],
  kinds: ["movie", "show"],
  statuses: ["rendered", "no_art"],
};

/** Shaped like `GET /api/actions/summary` answers, including the two flags the
 * registry ships switched OFF by default (`skipped` and `unknown_provenance`
 * -- src/autoposter/actions/flags.py). They are here because the page must
 * render whatever the server's registry holds rather than a list of its own:
 * `skipped` moved to default-off after this page's API landed, and no edit
 * here was needed for it. */
const SUMMARY = {
  total: 2,
  flags: [
    {
      code: "missing",
      label: "No art found",
      description: "No provider had artwork of this kind for this item.",
      default_on: true,
      instant: true,
      count: 1,
    },
    {
      code: "skipped",
      label: "Skipped",
      description: "The pipeline chose not to render this asset.",
      default_on: false,
      instant: true,
      count: 3,
    },
    {
      code: "language_miss",
      label: "Not the preferred language",
      description: "The language the ladder achieved is not this art kind's first choice.",
      default_on: true,
      instant: false,
      count: 1,
    },
    {
      code: "unknown_provenance",
      label: "Adopted, provenance unknown",
      description: "Artwork that was already on disk when this service took over.",
      default_on: false,
      instant: true,
      count: 9,
    },
  ],
};

const ROWS = {
  total: 2,
  limit: 50,
  offset: 0,
  items: [
    {
      item_id: 7,
      art_kind: "poster",
      title: "Dune: Part Two",
      library: "Movies",
      kind: "movie",
      status: "no_art",
      upload_status: "pending",
      provider: null,
      flags: ["missing"],
      details: ["no poster art on any provider"],
      dismissed: false,
      evidence: "a".repeat(64),
      quality_scored_at: null,
      updated_at: "2026-01-02T03:04:05Z",
    },
    {
      item_id: 8,
      art_kind: "background",
      title: "Heat",
      library: "Movies",
      kind: "movie",
      status: "rendered",
      upload_status: "uploaded",
      provider: "TVDB",
      flags: ["language_miss"],
      details: ["selected en; rank 1 in the order that rendered it"],
      dismissed: false,
      evidence: "b".repeat(64),
      quality_scored_at: "2026-01-02T03:04:05Z",
      updated_at: "2026-01-02T03:04:05Z",
    },
  ],
};

/** The dry run's own sentence, copied from the endpoint rather than invented:
 * `matched` counts flagged ROWS across the whole filter, `items` counts the
 * distinct items in THIS batch, and the two are separate numbers on purpose
 * (src/autoposter/api/action_center.py). */
const DRY_RUN = {
  status: "dry run",
  matched: 2,
  items: 2,
  enqueued: 0,
  detail:
    "2 flagged row(s) matched this filter; this batch covers 2 item(s) of it. Nothing was queued.",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Answers whatever the page asks for, and throws on anything it should not
 * ask for -- an unexpected path is a defect, not an empty table.
 *
 * A fresh `Response` per call rather than one shared object: a `Response` body
 * can be read once, so a single stubbed instance would answer the first
 * request and hand every later one an already-consumed stream. */
function stubFetch(overrides: Record<string, unknown> = {}) {
  const fetchMock = vi.fn(async (path: string, _init?: RequestInit) => {
    if (path === "/api/items/filters") return json(FILTERS);
    if (path.startsWith("/api/actions/summary")) return json(overrides.summary ?? SUMMARY);
    if (path.startsWith("/api/actions/bulk/rerender")) {
      return json(overrides.bulk ?? DRY_RUN);
    }
    if (path.startsWith("/api/actions/rerender")) {
      return json(overrides.rerender ?? { queued: true, job_id: 12 });
    }
    if (path.startsWith("/api/actions/rebuild")) {
      return json(
        overrides.rebuild ?? {
          status: "enqueued", matched: 1, selected: 1, cleared: 1,
          items: 1, enqueued: 1, rating_keys: ["7"],
        },
      );
    }
    if (path.startsWith("/api/actions/dismiss")) {
      if (overrides.dismissRefusal !== undefined) return json(overrides.dismissRefusal, 422);
      return json({ dismissed: true, evidence: "a".repeat(64) });
    }
    if (path.startsWith("/api/actions/undismiss")) return json({ dismissed: false });
    if (path.startsWith("/api/actions/backfill")) {
      return json(
        overrides.backfill ?? {
          status: "in_progress", done: 120, total: 300,
          queued_for_scoring: 30, unscored_total: 180, blocked: 0,
        },
      );
    }
    if (path.startsWith("/api/actions")) return json(overrides.rows ?? ROWS);
    throw new Error(`the page requested an unexpected path: ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

type FetchMock = ReturnType<typeof stubFetch>;

function renderPage() {
  return render(
    <MemoryRouter>
      <ActionCenter />
    </MemoryRouter>,
  );
}

/** Every path this page fetched, in order. */
function paths(fetchMock: FetchMock): string[] {
  return fetchMock.mock.calls.map((call) => call[0]);
}

/** The `RequestInit` of one recorded call. Throws rather than returning
 * undefined: a GET where the test expects a POST should read as "call 3 was
 * made with no init", not as an equality failure against `undefined`. */
function initOf(fetchMock: FetchMock, index: number): RequestInit {
  const found = fetchMock.mock.calls[index]?.[1];
  if (found === undefined) throw new Error(`call ${index} was made with no init`);
  return found;
}

function bodyOf(fetchMock: FetchMock, index: number): Record<string, unknown> {
  return JSON.parse(String(initOf(fetchMock, index).body)) as Record<string, unknown>;
}

beforeEach(() => {
  setToken(null);
});

describe("ActionCenter", () => {
  it("lists a flagged asset with its flag and the fact behind it", async () => {
    stubFetch();

    renderPage();

    expect(await screen.findByText("Dune: Part Two")).toBeInTheDocument();
    expect(screen.getByText("no poster art on any provider")).toBeInTheDocument();
    expect(
      screen.getByText("selected en; rank 1 in the order that rendered it"),
    ).toBeInTheDocument();
  });

  it("renders one count chip per flag the server knows about", async () => {
    stubFetch();

    renderPage();

    const missing = await screen.findByRole("button", { name: /No art found/ });
    expect(within(missing).getByText("1")).toBeInTheDocument();
    // Off by default and still offered: the operator opts into the adopted
    // population rather than being handed it.
    expect(
      within(screen.getByRole("button", { name: /Adopted, provenance unknown/ })).getByText("9"),
    ).toBeInTheDocument();
    // And the same for the flag that became default-off after the API landed:
    // the chip row is the server's registry, so that move cost this page
    // nothing.
    expect(
      within(screen.getByRole("button", { name: /Skipped/ })).getByText("3"),
    ).toBeInTheDocument();
  });

  it("says which flags cannot fire until a row re-renders", async () => {
    // The honest limitation, on the page and not only in the PR body: four
    // flags need bookkeeping that only a re-render writes.
    stubFetch();

    renderPage();

    const lazy = await screen.findByRole("button", { name: /Not the preferred language/ });
    expect(lazy).toHaveAttribute("title", expect.stringContaining("re-render"));
  });

  it("filters the queue by the chip that was clicked", async () => {
    const fetchMock = stubFetch();
    renderPage();
    await screen.findByText("Dune: Part Two");

    fireEvent.click(screen.getByRole("button", { name: /No art found/ }));

    await waitFor(() =>
      expect(paths(fetchMock).some((path) => path.includes("flag=missing"))).toBe(true),
    );
  });

  it("resets the offset when a filter changes", async () => {
    // A search narrowing the queue to two rows, read at offset 50, answers
    // with an empty list rather than an error -- which reads as "nothing is
    // flagged" when the truth is the opposite.
    // `total: 60` (rather than ROWS' own 2) so Next is genuinely enabled --
    // the pager disables it once `offset + rows.length >= total`, and with
    // only two rows total that would be true at offset 0 already.
    const fetchMock = stubFetch({ rows: { ...ROWS, total: 60 } });
    renderPage();
    await screen.findByText("Dune: Part Two");

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(paths(fetchMock).some((path) => path.includes("offset=50"))).toBe(true),
    );

    fireEvent.change(screen.getByLabelText("Library"), { target: { value: "Shows" } });

    await waitFor(() => {
      const last = paths(fetchMock).filter((path) => path.startsWith("/api/actions?")).at(-1);
      expect(last).toContain("library=Shows");
      expect(last).toContain("offset=0");
    });
  });

  it("keeps the later filter's rows on screen when the earlier request resolves last", async () => {
    // Two chip clicks in quick succession start two concurrent `load()`
    // runs. Without a latest-wins guard, whichever response lands LAST wins
    // regardless of which request it answers -- so the stale, first-clicked
    // filter's rows can overwrite the second click's rows on screen.
    const missingRows = { ...ROWS, items: [ROWS.items[0]], total: 1 };
    const languageRows = { ...ROWS, items: [ROWS.items[1]], total: 1 };

    let resolveMissing!: (value: Response) => void;
    let resolveLanguage!: (value: Response) => void;
    const missingPromise = new Promise<Response>((resolve) => {
      resolveMissing = resolve;
    });
    const languagePromise = new Promise<Response>((resolve) => {
      resolveLanguage = resolve;
    });

    const fetchMock = vi.fn(async (path: string) => {
      if (path === "/api/items/filters") return json(FILTERS);
      if (path.startsWith("/api/actions/summary")) return json(SUMMARY);
      if (path.includes("flag=missing")) return missingPromise;
      if (path.includes("flag=language_miss")) return languagePromise;
      if (path.startsWith("/api/actions")) return json(ROWS);
      throw new Error(`the page requested an unexpected path: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    await screen.findByText("Dune: Part Two");
    await screen.findByText("Heat");

    fireEvent.click(screen.getByRole("button", { name: /No art found/ }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some((call) => (call[0] as string).includes("flag=missing")))
        .toBe(true),
    );

    fireEvent.click(screen.getByRole("button", { name: /Not the preferred language/ }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((call) => (call[0] as string).includes("flag=language_miss")),
      ).toBe(true),
    );

    // The SECOND click's request resolves first...
    resolveLanguage(json(languageRows));
    await waitFor(() => expect(screen.queryByText("Dune: Part Two")).not.toBeInTheDocument());
    expect(screen.getByText("Heat")).toBeInTheDocument();

    // ...then the FIRST click's request resolves last. Its rows must not
    // overwrite what the second, later click asked for.
    resolveMissing(json(missingRows));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(screen.getByText("Heat")).toBeInTheDocument();
    expect(screen.queryByText("Dune: Part Two")).not.toBeInTheDocument();
  });

  it("shows the pager range against the server's own total", async () => {
    stubFetch();

    renderPage();

    expect(await screen.findByText("1–2 of 2")).toBeInTheDocument();
  });

  it("re-searches one row and re-reads the queue from the server", async () => {
    const fetchMock = stubFetch();
    renderPage();
    await screen.findByText("Dune: Part Two");
    const before = fetchMock.mock.calls.length;

    fireEvent.click(screen.getAllByRole("button", { name: "Re-search" })[0]);

    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(before + 1));
    const after = paths(fetchMock).slice(before);
    expect(after[0]).toBe("/api/actions/rerender");
    expect(initOf(fetchMock, before).method).toBe("POST");
    expect(bodyOf(fetchMock, before)).toEqual({ item_id: 7 });
    // Re-read rather than mutated locally: the server is the authority on
    // what is still flagged, and a re-search may legitimately change nothing.
    expect(after.some((path) => path.startsWith("/api/actions?"))).toBe(true);
    // The summary too -- a regression that re-fetched only the list would
    // leave the chip counts and the "N assets need attention" header stale.
    expect(after.some((path) => path.startsWith("/api/actions/summary"))).toBe(true);
  });

  it("reports a de-duplicated re-search without pretending it queued", async () => {
    const fetchMock = stubFetch({ rerender: { queued: false, job_id: null } });
    renderPage();
    await screen.findByText("Dune: Part Two");

    fireEvent.click(screen.getAllByRole("button", { name: "Re-search" })[0]);

    expect(await screen.findByText(/already queued/i)).toBeInTheDocument();
    expect(paths(fetchMock).filter((path) => path === "/api/actions/rerender")).toHaveLength(1);
  });

  it("dismisses a row through the dismiss endpoint and re-reads", async () => {
    const fetchMock = stubFetch();
    renderPage();
    await screen.findByText("Dune: Part Two");
    const before = fetchMock.mock.calls.length;

    fireEvent.click(screen.getAllByRole("button", { name: "Dismiss" })[0]);

    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(before + 1));
    expect(paths(fetchMock)[before]).toBe("/api/actions/dismiss");
    expect(bodyOf(fetchMock, before)).toEqual({
      item_id: 7,
      art_kind: "poster",
      flag: "missing",
    });
  });

  it("surfaces a refused dismissal in the server's own words", async () => {
    // `DismissBody.flag` is validated against the registry, so a chip this
    // page read from a summary served by an older build refuses as a 422 with
    // a Pydantic list. `api/client.ts` flattens that to "request failed with
    // 422", which tells the operator nothing; the sentence is in the detail.
    stubFetch({
      dismissRefusal: {
        detail: [
          {
            loc: ["body", "flag"],
            msg: "Value error, unknown flag 'missing'; this build has: skipped",
          },
        ],
      },
    });
    renderPage();
    await screen.findByText("Dune: Part Two");

    fireEvent.click(screen.getAllByRole("button", { name: "Dismiss" })[0]);

    expect(await screen.findByText(/unknown flag 'missing'/)).toBeInTheDocument();
  });

  it("restores a dismissed row through the undismiss endpoint", async () => {
    const dismissed = {
      ...ROWS,
      total: 1,
      items: [{ ...ROWS.items[0], dismissed: true }],
    };
    const fetchMock = stubFetch({ rows: dismissed });
    renderPage();
    await screen.findByText("Dune: Part Two");
    const before = fetchMock.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "Restore" }));

    await waitFor(() => expect(paths(fetchMock)[before]).toBe("/api/actions/undismiss"));
    expect(bodyOf(fetchMock, before)).toEqual({
      item_id: 7,
      art_kind: "poster",
    });
  });

  it("keeps a second row's buttons disabled through its own in-flight action", async () => {
    // Row 7's dismiss finishing first must not re-enable row 8's buttons
    // while row 8's own dismiss is still in flight -- a single shared busy
    // key would do exactly that.
    let resolveFirst!: (value: Response) => void;
    let resolveSecond!: (value: Response) => void;
    const firstPromise = new Promise<Response>((resolve) => {
      resolveFirst = resolve;
    });
    const secondPromise = new Promise<Response>((resolve) => {
      resolveSecond = resolve;
    });

    const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/api/items/filters") return json(FILTERS);
      if (path.startsWith("/api/actions/summary")) return json(SUMMARY);
      if (path.startsWith("/api/actions/dismiss")) {
        const body = JSON.parse(String(init?.body)) as { item_id: number };
        return body.item_id === 7 ? firstPromise : secondPromise;
      }
      if (path.startsWith("/api/actions")) return json(ROWS);
      throw new Error(`the page requested an unexpected path: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    await screen.findByText("Dune: Part Two");

    const [dismissRow7, dismissRow8] = screen.getAllByRole("button", { name: "Dismiss" });
    fireEvent.click(dismissRow7);
    fireEvent.click(dismissRow8);
    await waitFor(() => expect(dismissRow7).toBeDisabled());
    expect(dismissRow8).toBeDisabled();

    // Row 7's dismissal finishes first.
    resolveFirst(json({ dismissed: true, evidence: "a".repeat(64) }));
    await waitFor(() => expect(dismissRow7).not.toBeDisabled());

    // Row 8's own dismissal is still in flight; its buttons must stay
    // disabled regardless.
    expect(dismissRow8).toBeDisabled();

    resolveSecond(json({ dismissed: true, evidence: "b".repeat(64) }));
    await waitFor(() => expect(dismissRow8).not.toBeDisabled());
  });

  it("links each row to the item page, where the picker lives", async () => {
    // Replace is a deep link, not an inline picker: the picker is row 73's
    // and lives on the item page. A second copy of it here would be a second
    // thing to keep in step.
    stubFetch();

    renderPage();

    expect(await screen.findByRole("link", { name: "Dune: Part Two" })).toHaveAttribute(
      "href",
      "/items/7",
    );
  });

  it("counts a bulk re-search without queuing anything", async () => {
    const fetchMock = stubFetch();
    renderPage();
    await screen.findByText("Dune: Part Two");
    const before = fetchMock.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "Count what this would queue" }));

    expect(await screen.findByText(/Nothing was queued/)).toBeInTheDocument();
    expect(bodyOf(fetchMock, before).apply).toBe(false);
  });

  it("arms the bulk apply without posting anything", async () => {
    // The mutation proof: arming is a state change and nothing else. A button
    // that armed AND posted would pass every other test in this file.
    const fetchMock = stubFetch();
    renderPage();
    await screen.findByText("Dune: Part Two");
    const before = fetchMock.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "Re-search everything matching" }));

    expect(await screen.findByRole("button", { name: "Yes, queue them" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.length).toBe(before);
  });

  it("queues the batch once the arm is confirmed, then re-reads", async () => {
    const fetchMock = stubFetch({
      bulk: {
        status: "enqueued",
        matched: 2,
        items: 2,
        enqueued: 2,
        detail:
          "2 flagged row(s) matched this filter; this batch covered 2 item(s) of it, " +
          "queued 2; a re-search may legitimately find the same art",
      },
    });
    renderPage();
    await screen.findByText("Dune: Part Two");
    fireEvent.click(screen.getByRole("button", { name: "Re-search everything matching" }));
    const before = fetchMock.mock.calls.length;

    fireEvent.click(await screen.findByRole("button", { name: "Yes, queue them" }));

    await waitFor(() =>
      expect(screen.getByText(/covered 2 item\(s\) of it, queued 2/)).toBeInTheDocument(),
    );
    expect(bodyOf(fetchMock, before).apply).toBe(true);
    const after = paths(fetchMock).slice(before);
    expect(after.some((path) => path.startsWith("/api/actions?"))).toBe(true);
    // The summary too -- a regression that re-fetched only the list would
    // leave the chip counts and the "N assets need attention" header stale.
    expect(after.some((path) => path.startsWith("/api/actions/summary"))).toBe(true);
  });

  it("reports a batch the dedupe swallowed as the nothing it queued", async () => {
    // A second press while the first batch is still pending: the endpoint
    // answers 200 with `enqueued` below `items`, because the work IS queued.
    // The page renders those numbers rather than styling a successful answer
    // as a failure -- and rather than claiming two jobs it did not create.
    stubFetch({
      bulk: {
        status: "enqueued",
        matched: 2,
        items: 2,
        enqueued: 0,
        detail:
          "2 flagged row(s) matched this filter; this batch covered 2 item(s) of it, " +
          "queued 0; a re-search may legitimately find the same art",
      },
    });
    renderPage();
    await screen.findByText("Dune: Part Two");
    fireEvent.click(screen.getByRole("button", { name: "Re-search everything matching" }));

    fireEvent.click(await screen.findByRole("button", { name: "Yes, queue them" }));

    const detail = await screen.findByText(/covered 2 item\(s\) of it, queued 0/);
    expect(detail).toBeInTheDocument();
    // Not the ok pill: nothing was added, and a green answer beside "queued 0"
    // is the page contradicting the sentence next to it.
    expect(within(detail.closest(".action-bulk-result") as HTMLElement)
      .getByText("enqueued")).toHaveClass("pill-skipped");
  });

  it("clears a stale bulk result when a later bulk action fails", async () => {
    // A dry run's panel must not sit on screen, underneath the error, once a
    // subsequent apply against the same filters fails.
    let bulkCalls = 0;
    const fetchMock = vi.fn(async (path: string) => {
      if (path === "/api/items/filters") return json(FILTERS);
      if (path.startsWith("/api/actions/summary")) return json(SUMMARY);
      if (path.startsWith("/api/actions/bulk/rerender")) {
        bulkCalls += 1;
        if (bulkCalls === 1) return json(DRY_RUN);
        return json({ detail: "the database is unreachable" }, 503);
      }
      if (path.startsWith("/api/actions")) return json(ROWS);
      throw new Error(`the page requested an unexpected path: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    await screen.findByText("Dune: Part Two");

    fireEvent.click(screen.getByRole("button", { name: "Count what this would queue" }));
    await screen.findByText(/Nothing was queued/);
    expect(screen.getByRole("status")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Re-search everything matching" }));
    fireEvent.click(await screen.findByRole("button", { name: "Yes, queue them" }));

    expect(await screen.findByText("the database is unreachable")).toBeInTheDocument();
    expect(screen.queryByText(/Nothing was queued/)).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("disarms the bulk apply when a filter changes", async () => {
    // The grant was for the request the filters described. Changing them
    // changes the request, so the grant does not survive it.
    stubFetch();
    renderPage();
    await screen.findByText("Dune: Part Two");
    fireEvent.click(screen.getByRole("button", { name: "Re-search everything matching" }));
    await screen.findByRole("button", { name: "Yes, queue them" });

    fireEvent.change(screen.getByLabelText("Library"), { target: { value: "Shows" } });

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Yes, queue them" })).not.toBeInTheDocument(),
    );
  });

  it("rebuilds one row through the rebuild endpoint and re-reads", async () => {
    // The row press names the row, not a filter: the queue's unit is
    // (item, art kind), which is what `renders` keys uniquely.
    const fetchMock = stubFetch();
    renderPage();
    await screen.findByText("Dune: Part Two");
    const before = fetchMock.mock.calls.length;

    fireEvent.click(screen.getAllByRole("button", { name: "Rebuild" })[0]);

    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(before + 1));
    expect(paths(fetchMock)[before]).toBe("/api/actions/rebuild");
    expect(initOf(fetchMock, before).method).toBe("POST");
    expect(bodyOf(fetchMock, before)).toEqual({
      row: { item_id: 7, art_kind: "poster" },
      apply: true,
    });
    // Re-read rather than mutated locally: the rebuild has been QUEUED, not
    // run, so the server is still the authority on what is flagged.
    const after = paths(fetchMock).slice(before);
    expect(after.some((path) => path.startsWith("/api/actions?"))).toBe(true);
    expect(after.some((path) => path.startsWith("/api/actions/summary"))).toBe(true);
  });

  it("says nothing was cleared when a row rebuild press is idempotent", async () => {
    // The endpoint's own idempotence case -- a render already cleared and
    // queued, so a second press matches the row but clears and queues
    // nothing (`cleared: 0, enqueued: 0`). The old wording read "fingerprint
    // cleared" here even though nothing was: the same honesty rule as the
    // dry-run sentence, but for the row press.
    const fetchMock = stubFetch({
      rebuild: {
        status: "enqueued", matched: 1, selected: 1, cleared: 0,
        items: 1, enqueued: 0, rating_keys: ["7"],
      },
    });
    renderPage();
    await screen.findByText("Dune: Part Two");

    fireEvent.click(screen.getAllByRole("button", { name: "Rebuild" })[0]);

    expect(
      await screen.findByText("Dune: Part Two: already queued; nothing new to clear."),
    ).toBeInTheDocument();
    expect(paths(fetchMock).filter((path) => path === "/api/actions/rebuild")).toHaveLength(1);
  });

  it("counts what a bulk rebuild would clear without clearing anything", async () => {
    // `cleared` and `items` are the endpoint's honest preview -- one of the
    // two matched rows already has no fingerprint to clear -- so the
    // sentence must say what WOULD happen, not repeat the apply-shaped
    // template with fabricated zeros.
    const fetchMock = stubFetch({
      rebuild: {
        status: "dry run", matched: 2, selected: 2, cleared: 1,
        items: 2, enqueued: 0, rating_keys: [],
      },
    });
    renderPage();
    await screen.findByText("Dune: Part Two");
    const before = fetchMock.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "Count what a rebuild would clear" }));

    const detail = await screen.findByText(/1 of which would clear a fingerprint/);
    expect(detail).toBeInTheDocument();
    expect(screen.getByText(/Nothing was queued/)).toBeInTheDocument();
    expect(bodyOf(fetchMock, before).apply).toBe(false);
    // Nothing was actually cleared yet, so the pill must not read as a
    // success -- a green pill beside "Nothing was queued" would be the page
    // contradicting its own sentence.
    expect(within(detail.closest(".action-bulk-result") as HTMLElement)
      .getByText("dry run")).toHaveClass("pill-skipped");
  });

  it("arming either bulk action disarms the other", async () => {
    // The two panels share one grant: only one confirm may be on screen at a
    // time, so arming either one must withdraw the other's.
    stubFetch();
    renderPage();
    await screen.findByText("Dune: Part Two");

    fireEvent.click(screen.getByRole("button", { name: "Re-search everything matching" }));
    await screen.findByRole("button", { name: "Yes, queue them" });

    fireEvent.click(screen.getByRole("button", { name: "Rebuild everything matching" }));

    expect(await screen.findByRole("button", { name: "Yes, rebuild them" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Yes, queue them" })).not.toBeInTheDocument();

    // And the other direction: cancel the rebuild arm, arm it again, then
    // arm re-search -- the rebuild confirm must vanish in turn.
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Rebuild everything matching" }));
    await screen.findByRole("button", { name: "Yes, rebuild them" });

    fireEvent.click(screen.getByRole("button", { name: "Re-search everything matching" }));

    expect(await screen.findByRole("button", { name: "Yes, queue them" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Yes, rebuild them" })).not.toBeInTheDocument();
  });

  it("withdraws an armed bulk rebuild and clears its result when a filter changes", async () => {
    // The grant was for the filters described when it was armed; changing
    // them changes the request, so `refocus` withdraws it -- the same rule
    // "disarms the bulk apply when a filter changes" pins for re-search.
    stubFetch({
      rebuild: {
        status: "dry run", matched: 2, selected: 2, cleared: 1,
        items: 2, enqueued: 0, rating_keys: [],
      },
    });
    renderPage();
    await screen.findByText("Dune: Part Two");

    fireEvent.click(screen.getByRole("button", { name: "Count what a rebuild would clear" }));
    await screen.findByText(/Nothing was queued/);

    fireEvent.click(screen.getByRole("button", { name: "Rebuild everything matching" }));
    await screen.findByRole("button", { name: "Yes, rebuild them" });

    fireEvent.change(screen.getByLabelText("Library"), { target: { value: "Shows" } });

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Yes, rebuild them" })).not.toBeInTheDocument(),
    );
    expect(screen.queryByText(/Nothing was queued/)).not.toBeInTheDocument();
  });

  it("arms the bulk rebuild without posting anything", async () => {
    // The mutation proof, the same one the re-search arm carries: arming is a
    // state change and nothing else.
    const fetchMock = stubFetch();
    renderPage();
    await screen.findByText("Dune: Part Two");
    const before = fetchMock.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "Rebuild everything matching" }));

    expect(await screen.findByRole("button", { name: "Yes, rebuild them" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.length).toBe(before);
    // Only one arm can be open at a time, so there is never a second Cancel
    // and never two confirm buttons to press by accident.
    expect(screen.queryByRole("button", { name: "Yes, queue them" })).not.toBeInTheDocument();
  });

  it("rebuilds everything matching once the arm is confirmed, then re-reads", async () => {
    const fetchMock = stubFetch({
      rebuild: {
        status: "enqueued", matched: 2, selected: 2, cleared: 2,
        items: 2, enqueued: 2, rating_keys: ["7", "8"],
      },
    });
    renderPage();
    await screen.findByText("Dune: Part Two");
    fireEvent.click(screen.getByRole("button", { name: "Rebuild everything matching" }));
    const before = fetchMock.mock.calls.length;

    fireEvent.click(await screen.findByRole("button", { name: "Yes, rebuild them" }));

    await waitFor(() => expect(screen.getByText(/cleared 2/)).toBeInTheDocument());
    expect(bodyOf(fetchMock, before).apply).toBe(true);
    const after = paths(fetchMock).slice(before);
    expect(after.some((path) => path.startsWith("/api/actions?"))).toBe(true);
    expect(after.some((path) => path.startsWith("/api/actions/summary"))).toBe(true);
  });

  it("never offers the retired word the row was filed under", async () => {
    // Row 233's ruling is that "delete" MEANS rebuild, and nothing here
    // deletes anything -- so the word is retired from this page's copy. A
    // button labelled Delete would promise an unlink the endpoint does not do.
    stubFetch();

    renderPage();
    await screen.findByText("Dune: Part Two");

    // `queryAll`, not `query`: the singular form THROWS on more than one
    // match, so a regression that added two Delete labels would fail with
    // "found multiple elements" rather than with this test's own sentence.
    expect(screen.queryAllByRole("button", { name: /delete/i })).toHaveLength(0);
    expect(screen.queryAllByText(/delete/i)).toHaveLength(0);
  });

  it("renders an empty state when nothing is flagged", async () => {
    stubFetch({ rows: { total: 0, limit: 50, offset: 0, items: [] } });

    renderPage();

    expect(await screen.findByText(/Nothing needs attention/)).toBeInTheDocument();
  });

  it("reports a failed load rather than rendering an empty queue", async () => {
    const fetchMock = vi.fn(async (path: string) => {
      if (path === "/api/items/filters") return json(FILTERS);
      if (path.startsWith("/api/actions/summary")) return json(SUMMARY);
      return json({ detail: "the database is unreachable" }, 503);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    expect(await screen.findByText("the database is unreachable")).toBeInTheDocument();
    expect(screen.queryByText(/Nothing needs attention/)).not.toBeInTheDocument();
  });

  it("keeps the table scrollable and the row actions grouped", async () => {
    // jsdom computes no layout, so what is assertable is that the hooks the
    // shared CSS hangs off are on the right nodes. The widths themselves were
    // checked in a browser, exactly as the Failures sweep did.
    stubFetch();

    renderPage();
    const title = await screen.findByText("Dune: Part Two");

    expect(title.closest("table")?.parentElement).toHaveClass("table-scroll");
    const actions = screen.getAllByRole("button", { name: "Re-search" })[0].parentElement;
    expect(actions).toHaveClass("row-actions");
  });

  it("shows how much of the library has been scored", async () => {
    stubFetch();

    renderPage();

    expect(await screen.findByText("120 of 300 assets scored")).toBeInTheDocument();
  });

  it("triggers one backfill batch and re-reads the progress", async () => {
    const fetchMock = stubFetch();
    renderPage();
    await screen.findByText("120 of 300 assets scored");
    const before = fetchMock.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "Score the next batch" }));

    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(before + 1));
    expect(paths(fetchMock)[before]).toBe("/api/actions/backfill");
    expect(initOf(fetchMock, before).method).toBe("POST");
    // Re-read: the progress is the server's, and the batch it queued has not
    // run yet, so the page must not compute a number of its own.
    expect(paths(fetchMock).slice(before + 1)).toContain("/api/actions/backfill");
  });

  it("offers nothing to press once every asset is scored", async () => {
    stubFetch({
      backfill: {
        status: "complete", done: 300, total: 300,
        queued_for_scoring: 0, unscored_total: 0,
      },
    });

    renderPage();

    expect(
      await screen.findByRole("button", { name: "Score the next batch" }),
    ).toBeDisabled();
  });

  it("shows how much of the unscored population is already queued for scoring", async () => {
    stubFetch();

    renderPage();

    expect(
      await screen.findByText("30 of 180 unscored asset(s) queued for scoring"),
    ).toBeInTheDocument();
  });

  it("shows the blocked count with a link to Failures when it is non-zero", async () => {
    // Roadmap: the unscorable-floor investigation's fix 4. `blocked` names
    // rows a press cannot move -- only an operator acting on Failures can --
    // so the panel must say so, and point there, whenever it is non-zero.
    stubFetch({
      backfill: {
        status: "in_progress", done: 120, total: 300,
        queued_for_scoring: 30, unscored_total: 180, blocked: 7,
      },
    });

    renderPage();

    const notice = await screen.findByText(/7 blocked/);
    expect(notice).toBeInTheDocument();
    expect(within(notice).getByRole("link", { name: "Failures" })).toHaveAttribute(
      "href", "/failures",
    );
  });

  it("shows no blocked line when nothing is blocked", async () => {
    stubFetch();

    renderPage();

    await screen.findByText("120 of 300 assets scored");
    expect(screen.queryByText(/blocked/)).not.toBeInTheDocument();
  });

  it("reports the post-press queued-for-scoring depth in the batch feedback", async () => {
    // A live operator mid-run, pressing the button, cannot see the queue's
    // actual depth from `enqueued` alone -- the fix this pins. The POST's own
    // `detail` carries the depth after this press's enqueue, and the page
    // must show it, not compute or restate a number of its own.
    stubFetch({
      backfill: {
        status: "enqueued", selected: 500, enqueued: 500, done: 8214, total: 17264,
        queued_for_scoring: 620, unscored_total: 9050,
        detail: "queued 500 more; 620 of 9050 unscored now queued for scoring",
      },
    });
    renderPage();
    await screen.findByText("620 of 9050 unscored asset(s) queued for scoring");

    fireEvent.click(screen.getByRole("button", { name: "Score the next batch" }));

    expect(
      await screen.findByText("queued 500 more; 620 of 9050 unscored now queued for scoring"),
    ).toBeInTheDocument();
  });
});
