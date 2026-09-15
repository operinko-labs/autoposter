/** Provider attribution is a licence condition, not a design detail.
 *
 * TMDB's terms require their exact notice wording and their logo; TheTVDB's
 * require attribution carrying a direct link to their site. Both are
 * acceptance criteria for this phase, so they are asserted verbatim -- a test
 * that matched loosely would let a paraphrase through, and a paraphrase is
 * the thing the terms forbid.
 */
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { STALE_SAVE_NOTE } from "../api/overrides";
import { DRIFT_NOTICE } from "./DriftNotice";
import {
  IMPACT_CAVEAT,
  REDACTED_EDIT_NOTE,
  Settings,
  TMDB_NOTICE,
} from "./Settings";

const REDACTED = "***REDACTED***";

/** Deliberately includes shapes the renderer must handle generically: nested
 * objects two levels deep, a scalar list, booleans in both states, an empty
 * string, and (in the unknown-key test below) a section the component has
 * never seen. */
const CONFIG = {
  workers: 5,
  plex: { url: "http://plex:32400", excluded_libraries: ["Muskarit", "Photos"] },
  badges: { enabled: true, upload_to_plex: false },
  notifications: { enabled: false, url: "" },
  artwork: { use_logo: true, poster: { text: { font: "Comfortaa-Medium.ttf" } } },
  secrets: { plex_token: REDACTED, tmdb_api_key: REDACTED },
};

function stubConfig(body: unknown = CONFIG) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
}

beforeEach(() => {
  setToken(null);
  // The open accordion is remembered in localStorage, and jsdom keeps that
  // for the life of the file's window -- without this, one test's open
  // section is what the next test's mount reads back.
  window.localStorage.clear();
});

/** The config fetch resolves on the next microtask, so rendering without
 * awaiting leaves a state update outside act(). */
async function renderSettings() {
  await act(async () => {
    render(<Settings />);
  });
}

/** The page is a tab bar of collapsed accordions, so reaching a field takes
 * two clicks: its tab, then its section. Every test below that touches a
 * field names both, which is the tab map asserted in passing -- a section
 * that moved tabs turns the test that reaches it red rather than leaving it
 * to find the field wherever it ended up. */
function openSection(tab: string, section?: string) {
  fireEvent.click(screen.getByRole("tab", { name: tab }));
  if (section !== undefined) {
    fireEvent.click(screen.getByRole("button", { name: section }));
  }
}

/** The same, awaited, for a tab carrying a panel that fetches on mount.
 *
 * The System tab's backup panel does, and it mounts with the tab rather than
 * with the page now -- so the click is what has to settle. An update landing
 * after the test that started it has ended is a leak, not a warning: this file
 * shares one jsdom window across every test in it. */
async function openSettled(tab: string, section?: string) {
  // The tab click gets its own act: the accordion it reveals is not in the
  // DOM until that one has flushed, so a second click batched into the same
  // act would have nothing to find.
  await act(async () => {
    fireEvent.click(screen.getByRole("tab", { name: tab }));
  });
  if (section !== undefined) {
    fireEvent.click(screen.getByRole("button", { name: section }));
  }
}

describe("Settings attribution", () => {
  it("renders TMDB's notice with their required wording, exactly", async () => {
    stubConfig();
    await renderSettings();
    // The notices are a licence condition of showing TMDB's and TheTVDB's
    // artwork and metadata, so they sit on the tab where that lives.
    openSection("Artwork");

    expect(
      screen.getByText(
        "This product uses TMDB and the TMDB APIs but is not endorsed, " +
          "certified, or otherwise approved by TMDB.",
      ),
    ).toBeInTheDocument();
    // The constant the page renders and the string the terms specify are the
    // same string; a reworded constant would pass the check above only by
    // also failing this one.
    expect(TMDB_NOTICE).toBe(
      "This product uses TMDB and the TMDB APIs but is not endorsed, " +
        "certified, or otherwise approved by TMDB.",
    );
  });

  it("renders the TMDB logo from a hashed asset, not from the dist root", async () => {
    stubConfig();
    await renderSettings();
    openSection("Artwork");

    const logo = screen.getByAltText("TMDB") as HTMLImageElement;
    // `/tmdb-logo.png` at the root of dist/ is not served by
    // src/autoposter/api/spa.py -- the catch-all answers it with index.html
    // as a 200, so the image silently renders broken. Bundled assets live
    // under /assets, which is mounted.
    expect(logo.getAttribute("src")).not.toBe("/tmdb-logo.png");
    expect(logo.getAttribute("src")).toBeTruthy();
  });

  it("links TheTVDB attribution directly to thetvdb.com", async () => {
    stubConfig();
    await renderSettings();
    openSection("Artwork");

    const link = screen.getByRole("link", { name: /TheTVDB/i }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("https://thetvdb.com");
  });
});

describe("Settings configuration", () => {
  it("renders titled sections with rows, not a JSON dump", async () => {
    stubConfig();
    await renderSettings();

    // Top-level objects become accordions, each on the tab its section maps
    // to, with the heading carried by the accordion's own toggle.
    openSection("Servers", "Plex");
    expect(screen.getByRole("heading", { name: "Plex" })).toBeInTheDocument();
    expect(screen.getByText("Excluded libraries")).toBeInTheDocument();
    // The values are editable fields now rather than text, so they are read
    // off the widgets -- the row labels above are still plain text.
    expect(screen.getByLabelText("plex.excluded_libraries[0]")).toHaveValue(
      "Muskarit",
    );
    expect(screen.getByLabelText("plex.excluded_libraries[1]")).toHaveValue(
      "Photos",
    );
    expect(screen.getByLabelText("plex.url")).toHaveValue("http://plex:32400");
    // Asserted here, with a section open and its rows on screen, rather than
    // only at the end: a dump would appear inside an accordion body, so a
    // check run over a page of closed headers proves nothing.
    expect(document.querySelector("pre")).toBeNull();
    expect(document.body.textContent).not.toContain("{");

    openSection("Artwork", "Badges");
    expect(screen.getByRole("heading", { name: "Badges" })).toBeInTheDocument();
    // ...nested objects become subsections, recursively (artwork -> poster ->
    // text), with snake_case keys turned into words.
    openSection("Artwork", "Artwork");
    expect(screen.getByRole("heading", { name: "Poster" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Text" })).toBeInTheDocument();
    expect(screen.getByLabelText("artwork.poster.text.font")).toHaveValue(
      "Comfortaa-Medium.ttf",
    );
    // And again over the deepest nesting on the page, which is where a dump
    // would be the tempting shortcut.
    expect(document.querySelector("pre")).toBeNull();
    expect(document.body.textContent).not.toContain("{");
  });

  it("renders a redacted value as a badge, never as the marker or a field", async () => {
    // The marker is a state, not a value, and two branches carry that: the
    // field builder refuses to put a widget over it, and the value renderer
    // shows a badge in its place. A page that lost either would offer an edit
    // whose save stores the literal marker over the real setting.
    stubConfig({ ...CONFIG, notifications: { enabled: false, url: REDACTED } });
    await renderSettings();
    openSection("Integrations", "Notifications");

    const row = rowOf(screen.getByText("Url"));
    expect(within(row).getByText("redacted")).toBeInTheDocument();
    // No widget of any kind, so there is nothing to type the marker back in.
    expect(within(row).queryByRole("textbox")).toBeNull();
    expect(screen.queryByLabelText("notifications.url")).toBeNull();
    // And the marker itself never reaches the page as text -- asserted with
    // the accordion that holds it open, so it is a real read of a rendered
    // value rather than of a page showing no values at all.
    expect(document.body.textContent).not.toContain(REDACTED);
  });

  it("renders no secrets section at all, and never the marker string", async () => {
    stubConfig();
    await renderSettings();

    // The config's own `secrets` block is no longer rendered on any tab: an
    // all-redacted read-only panel is a weaker answer than one that can set a
    // secret. Asserted across all seven rather than on whichever one the test
    // happened to be looking at.
    for (const tab of [
      "Servers",
      "Libraries",
      "Artwork",
      "Collections",
      "Metadata",
      "Integrations",
      "System",
    ]) {
      await openSettled(tab);
      expect(screen.queryByRole("button", { name: "Secrets" })).toBeNull();
    }
    // And the literal marker never reaches the page as a value.
    expect(document.body.textContent).not.toContain(REDACTED);
  });

  it("renders booleans as toggles reflecting their state", async () => {
    stubConfig();
    await renderSettings();

    // Was: on/off pills. The editor makes them toggles, so the state that
    // used to be a word is now the checkbox's own checked-ness. One accordion
    // is open per tab, so a section per pair of assertions.
    openSection("Artwork", "Badges");
    expect(screen.getByLabelText("badges.enabled")).toBeChecked();
    expect(screen.getByLabelText("badges.upload_to_plex")).not.toBeChecked();
    openSection("Artwork", "Artwork");
    expect(screen.getByLabelText("artwork.use_logo")).toBeChecked();
    openSection("Integrations", "Notifications");
    expect(screen.getByLabelText("notifications.enabled")).not.toBeChecked();
  });

  it("renders an empty string as an empty text field, not as (not set)", async () => {
    stubConfig();
    await renderSettings();

    // An empty string is a value the operator can type into; only a genuine
    // null (which the API refuses as an override) stays a read-only marker.
    openSection("Integrations", "Notifications");
    expect(screen.getByLabelText("notifications.url")).toHaveValue("");
    expect(screen.queryByText("(not set)")).toBeNull();
  });

  it("renders a section it has never seen, without a frontend change", async () => {
    // The renderer must be driven by the response's shape, not a hard-coded
    // field list -- the config schema grows every phase. This key exists
    // nowhere in the component.
    stubConfig({
      frobnicator: { warp_factor: 9, reticulate_splines: true },
    });
    await renderSettings();

    // System is `tabForSection`'s fallback, which is the half of the tab map
    // that keeps an unassigned section reachable rather than invisible.
    await openSettled("System", "Frobnicator");
    expect(
      screen.getByRole("heading", { name: "Frobnicator" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Warp factor")).toBeInTheDocument();
    // ...and it is editable on the same shape-driven terms, with no entry in
    // any widget table.
    expect(screen.getByLabelText("frobnicator.warp_factor")).toHaveValue(9);
    expect(screen.getByLabelText("frobnicator.reticulate_splines")).toBeChecked();
  });

  it("surfaces a failed config read instead of loading forever", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "config unreadable" }), {
          status: 500,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    render(<Settings />);

    expect(await screen.findByText("config unreadable")).toBeInTheDocument();
    // The attribution is static and must survive an API failure -- it is a
    // licence condition, not a view of server data. It lives on the Artwork
    // tab now, and that tab renders it without waiting for a config load that
    // may never arrive.
    openSection("Artwork");
    expect(screen.getByAltText("TMDB")).toBeInTheDocument();
  });

  it("offers backup and previous versions on the settings page, not a new route", async () => {
    // The panel does its own fetching, against a different endpoint than the
    // page's config load -- routed per URL, and a fresh Response per call, so
    // this actually exercises the panel against the snapshot list's real
    // shape rather than a config body it happens to tolerate.
    const snapshot = { id: 42, created_at: "2026-09-03T14:22:00+00:00", path_count: 3, reason: "save" };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        const body = path === "/api/config/snapshots" ? [snapshot] : CONFIG;
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    await renderSettings();
    // The panel mounts with the tab rather than with the page now, and it
    // fetches its own snapshot list on mount -- so the click is what has to
    // be awaited.
    await act(async () => {
      await openSettled("System");
    });

    expect(
      screen.getByRole("heading", { name: "Backup and previous versions" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/3 settings/)).toBeInTheDocument();
  });

  it("carries the webhook secret rotation panel", async () => {
    stubConfig();
    await renderSettings();
    await openSettled("System");

    expect(
      screen.getByRole("button", { name: /rotate webhook secret/i }),
    ).toBeInTheDocument();
  });
});

/** The editor's own fixture: it carries the keys the enriched GET adds that
 * are not settings, one frozen prefix with the server's reason, and one field
 * of each editable value type. */
const EDITOR_CONFIG = {
  version: "abc123",
  workers: 5,
  plex: {
    url: "http://plex:32400",
    excluded_libraries: ["Muskarit", "Photos"],
    resolve_max_attempts: 10,
  },
  badges: { enabled: true },
  artwork: { poster: { text: { min_point_size: 20 } } },
  secrets: { plex_token: REDACTED },
  restart_paths: [] as string[],
  frozen_paths: {
    workers: "the worker pool is sized once, at startup",
    plex: "the Plex client is built once at startup",
  },
  field_descriptions: {
    workers: "How many render workers run in parallel.",
    version: "The hash of every setting that changes what a render produces.",
    "plex.url": "The base URL of the Plex server this service manages.",
    "plex.resolve_max_attempts": "How many times a failed Plex lookup is retried.",
    "secrets.plex_token": "The Plex authentication token this service connects with.",
  },
  computed_paths: ["version"],
  live_paths: ["plex.resolve_max_attempts"],
};

/** The document the page seeds from `EDITOR_CONFIG`: the whole served
 * configuration minus the keys that are not settings.
 *
 * Spelled out rather than computed, because what these tests are checking is
 * that the page sends the whole store back. Deriving it from the same helper
 * the page uses would make every assertion below agree with the page by
 * construction, including when both are wrong. */
const SEEDED_DOCUMENT = {
  version: "abc123",
  workers: 5,
  plex: {
    url: "http://plex:32400",
    excluded_libraries: ["Muskarit", "Photos"],
    resolve_max_attempts: 10,
  },
  badges: { enabled: true },
  artwork: { poster: { text: { min_point_size: 20 } } },
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Routes by method rather than by call order: a save is a PUT followed by a
 * re-read GET, and a test that answered "first call, second call" would pass
 * against a page that sent them in either order. `config` may be a list, in
 * which case each GET takes the next one and the last repeats. */
function stubApi({
  config = EDITOR_CONFIG as unknown,
  put = json({ version_before: "abc123", version_after: "abc123", restart_required: [] }),
}: { config?: unknown | unknown[]; put?: Response } = {}) {
  const configs = Array.isArray(config) ? [...config] : [config];
  const fetchMock = vi.fn((_input: string, init?: RequestInit) => {
    if ((init?.method ?? "GET") === "GET") {
      return Promise.resolve(json(configs.length > 1 ? configs.shift() : configs[0]));
    }
    return Promise.resolve(put.clone());
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** `stubApi`, but able to answer an endpoint the page reads BESIDES the
 * config. Every GET this file's other helpers make is answered with the config
 * body, which is exactly why the drift notice renders nothing in all of them:
 * `file_present` is `undefined`. A test about a panel with its own endpoint has
 * to route by URL or it is testing the stub. */
function stubByUrl(
  responses: Record<string, unknown>,
  config: unknown = { ...EDITOR_CONFIG, overrides_revision: "rev-1" },
  post: Response = json({ version_before: "abc123", version_after: "def456" }),
) {
  const fetchMock = vi.fn((input: string, init?: RequestInit) => {
    if ((init?.method ?? "GET") !== "GET") return Promise.resolve(post.clone());
    return Promise.resolve(
      Object.hasOwn(responses, input) ? json(responses[input]) : json(config),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The body of the one PUT the page sent. */
function putDocument(fetchMock: ReturnType<typeof stubApi>): unknown {
  const calls = fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT");
  expect(calls).toHaveLength(1);
  expect(calls[0][0]).toBe("/api/config/overrides");
  return JSON.parse(String(calls[0][1]?.body));
}

async function save() {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
  });
}

/** The row a field sits in, so an inline error can be asserted to be *at* the
 * field rather than merely somewhere on a page that also shows the field. */
function rowOf(field: HTMLElement): HTMLElement {
  const row = field.closest(".config-row");
  if (row === null) throw new Error("field is not inside a config row");
  return row as HTMLElement;
}

describe("Settings editor", () => {
  it("sends the whole document with the touched path changed in it", async () => {
    const fetchMock = stubApi();
    await renderSettings();
    openSection("Artwork", "Artwork");

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await save();

    // The document IS the store: there is no file layer under it, so a body
    // carrying only the touched path would delete `workers`, `plex` and every
    // other setting the operator did not happen to visit.
    expect(putDocument(fetchMock)).toEqual({
      document: {
        ...SEEDED_DOCUMENT,
        artwork: { poster: { text: { min_point_size: 24 } } },
      },
    });
  });

  it("reports the version move, and leaves the restart list to the banner", async () => {
    // The save's own `restart_required` is its difference against the running
    // generation; the banner renders the STORE's list, which is measured
    // against what this process booted on. Repeating the first beside the
    // second would be two answers to one question, and they disagree the
    // first time a setting is edited and put back.
    stubApi({
      config: [EDITOR_CONFIG, { ...EDITOR_CONFIG, restart_paths: ["workers"] }],
      put: json({
        version_before: "abc123",
        version_after: "def456",
        restart_required: ["workers"],
      }),
    });
    await renderSettings();
    await openSettled("System", "General");

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await save();

    expect(screen.getByText(/abc123 → def456/)).toBeInTheDocument();
    expect(
      screen.getByText(
        "These settings are saved and take effect at the next restart: workers",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Restart required to apply/)).toBeNull();
  });

  it("puts an inert change on the same banner, not a second sentence", async () => {
    // api_docs_enabled is built into the application object before any
    // override is read, so no swap reaches it even in part -- the restart
    // that does apply it reads the stored document. The save response still
    // reports it apart from `restart_required`, because the two land at
    // different moments; the store's restart list carries both, because a
    // restart is what applies either and the banner asks one question.
    stubApi({
      config: [
        EDITOR_CONFIG,
        { ...EDITOR_CONFIG, restart_paths: ["api_docs_enabled"] },
      ],
      put: json({
        version_before: "abc123",
        version_after: "abc123",
        restart_required: [],
        inert: ["api_docs_enabled"],
      }),
    });
    await renderSettings();
    await openSettled("System", "General");

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await save();

    expect(
      screen.getByText(
        "These settings are saved and take effect at the next restart: api_docs_enabled",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Takes effect at the next restart:/)).toBeNull();
  });

  it("puts the banner above the tab strip, so it is visible from every tab", async () => {
    // The operator who needs to see a waiting restart is rarely the one still
    // looking at the tab that caused it.
    stubApi({ config: { ...EDITOR_CONFIG, restart_paths: ["jellyfin", "workers"] } });
    await renderSettings();

    const banner = screen.getByText(
      "These settings are saved and take effect at the next restart: jellyfin, workers",
    );
    const tabs = screen.getByRole("tablist");
    expect(
      banner.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("will not restart while the editor is holding an unsaved edit", async () => {
    // The press would re-seed the editor from the server and take the typing
    // with it. The pending bar below offers both ways out.
    stubApi({ config: { ...EDITOR_CONFIG, restart_paths: ["workers"] } });
    await renderSettings();
    await openSettled("System", "General");

    expect(screen.getByRole("button", { name: "Restart now" })).toBeEnabled();
    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });

    expect(screen.getByRole("button", { name: "Restart now" })).toBeDisabled();
    expect(
      screen.getByText(/Save or discard the changes below first/),
    ).toBeInTheDocument();
  });

  it("puts the drift notice on the System tab and nowhere else", async () => {
    // Routed by URL rather than answering every GET with the config: the
    // notice reads a different endpoint, and a stub that answered it with the
    // config would render nothing here whatever the page did with it.
    const fetchMock = stubByUrl({
      "/api/config/drift": {
        file_present: true,
        differs: true,
        paths: ["workers"],
        path: "/config/autoposter.yaml",
        file_revision: "file-rev-1",
      },
    });
    await renderSettings();

    expect(screen.queryByText(DRIFT_NOTICE)).toBeNull();
    await openSettled("System");
    expect(screen.getByText(DRIFT_NOTICE)).toBeInTheDocument();

    // And it is wired to the page: the import carries the revision the page
    // seeded from, and the page re-reads afterwards.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Import the file" }));
    });
    const posted = fetchMock.mock.calls.filter(
      ([input, init]) =>
        input === "/api/config/drift/import" && init?.method === "POST",
    );
    expect(posted).toHaveLength(1);
    expect(JSON.parse(String(posted[0][1]?.body))).toEqual({
      confirm: false,
      expected_revision: "rev-1",
      expected_file_revision: "file-rev-1",
    });
  });

  it("lands a 422 inline at the field its path names", async () => {
    stubApi({
      put: json(
        { detail: [{ path: "plex.url", message: "Input should be a valid URL" }] },
        422,
      ),
    });
    await renderSettings();
    openSection("Servers", "Plex");

    fireEvent.change(screen.getByLabelText("plex.url"), {
      target: { value: "not a url" },
    });
    await save();

    expect(
      within(rowOf(screen.getByLabelText("plex.url"))).getByText(
        "Input should be a valid URL",
      ),
    ).toBeInTheDocument();
    // A rejected save must not be reported as a save.
    expect(screen.queryByText(/Saved/)).toBeNull();
  });

  it("maps FastAPI's own 422 shape to the same dotted path", async () => {
    // A malformed body is rejected by the request validator before the
    // handler runs, and that error has `loc`/`msg`, not `path`/`message`.
    // Both shapes arrive at this page through the same endpoint.
    stubApi({
      put: json(
        {
          detail: [
            {
              loc: ["body", "document", "plex", "url"],
              msg: "Input should be a valid URL",
              type: "url_parsing",
            },
          ],
        },
        422,
      ),
    });
    await renderSettings();
    openSection("Servers", "Plex");

    fireEvent.change(screen.getByLabelText("plex.url"), {
      target: { value: "not a url" },
    });
    await save();

    expect(
      within(rowOf(screen.getByLabelText("plex.url"))).getByText(
        "Input should be a valid URL",
      ),
    ).toBeInTheDocument();
  });

  it("marks an edited frozen field as needing a restart, with the server's reason", async () => {
    stubApi();
    await renderSettings();
    await openSettled("System", "General");

    // Unedited, the marker would be noise: nothing is pending. No section
    // header carries one either -- `frozen_paths` here names `workers` and
    // `plex`, and neither is a section on this tab.
    expect(screen.queryByText("restart to apply")).toBeNull();

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });

    const marker = within(rowOf(screen.getByLabelText("workers"))).getByText(
      "restart to apply",
    );
    expect(marker).toHaveAttribute("title", "the worker pool is sized once, at startup");
    // A live path stays unmarked -- a page that marked everything would be
    // telling the operator to restart for a change that already took effect.
    openSection("Artwork", "Artwork");
    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    expect(
      within(rowOf(screen.getByLabelText("artwork.poster.text.min_point_size")))
        .queryByText("restart to apply"),
    ).toBeNull();
  });

  it("hangs each setting's description off its label as hover text", async () => {
    stubApi();
    await renderSettings();
    await openSettled("System", "General");

    const label = within(rowOf(screen.getByLabelText("workers"))).getByText(
      "Workers",
    );
    expect(label).toHaveAttribute(
      "title",
      "How many render workers run in parallel.",
    );
    // A path the server described nothing for gets no empty tooltip: an empty
    // `title` is a hover that opens onto nothing.
    openSection("Artwork", "Badges");
    expect(
      within(rowOf(screen.getByLabelText("badges.enabled"))).getByText("Enabled"),
    ).not.toHaveAttribute("title");
  });

  it("hangs a description off a row with no widget, since the text is all it shows", async () => {
    stubApi();
    await renderSettings();
    await openSettled("System", "General");

    // `version` is a computed path, so it gets no editor and renders nothing
    // but its value -- the description is the one informative thing left on
    // the row, which is why `descriptions` is a prop of `ConfigSections`
    // independent of `editor`, which such a row deliberately does not get.
    const label = within(rowOf(screen.getByText("abc123"))).getByText("Version");
    expect(label).toHaveAttribute(
      "title",
      "The hash of every setting that changes what a render produces.",
    );
  });

  it("renders a computed path read-only rather than offering an inert edit", async () => {
    stubApi();
    await renderSettings();
    await openSettled("System", "General");

    // `version` is derived from the other settings; an override on it is
    // recomputed away, so the editor must not present it as a field.
    expect(screen.queryByLabelText("version")).toBeNull();
    expect(screen.getByText("abc123")).toBeInTheDocument();
  });

  it("does not demand a restart for a live path inside a frozen section", async () => {
    stubApi();
    await renderSettings();
    openSection("Servers", "Plex");

    // `plex` is frozen as a whole, but this one path is read per use -- the
    // server says so in `live_paths`, and the save response already omits it.
    fireEvent.change(screen.getByLabelText("plex.resolve_max_attempts"), {
      target: { value: "12" },
    });
    expect(
      within(rowOf(screen.getByLabelText("plex.resolve_max_attempts")))
        .queryByText("restart to apply"),
    ).toBeNull();
    // A sibling under the same frozen prefix still says it.
    fireEvent.change(screen.getByLabelText("plex.url"), {
      target: { value: "http://plex:32401" },
    });
    expect(
      within(rowOf(screen.getByLabelText("plex.url"))).getByText("restart to apply"),
    ).toBeInTheDocument();
  });

  it("renders no overridden pill, because the served config carries no provenance", async () => {
    // The store holds the whole configuration, so every value on the page came
    // from it and marking them all would say nothing. The question a row
    // answers is "what is this set to", and the answer is the field.
    stubApi({ config: [{ ...EDITOR_CONFIG, workers: 9 }] });
    await renderSettings();
    await openSettled("System", "General");

    expect(screen.queryByText("overridden")).toBeNull();
    expect(
      screen.queryByRole("button", { name: /Clear override for/ }),
    ).toBeNull();
    expect(screen.getByLabelText("workers")).toHaveValue(9);
  });

  it("never makes a secret editable", async () => {
    stubApi();
    await renderSettings();
    await openSettled("System", "General");

    // Stronger than the read-only panel this replaces: there is no secrets
    // section on the page at all, so there is nothing that could drift into
    // an editable field, and no row for one either.
    expect(screen.queryByRole("heading", { name: "Secrets" })).toBeNull();
    expect(screen.queryByLabelText("secrets.plex_token")).toBeNull();
    expect(screen.queryByText("Plex token")).toBeNull();
  });

  it("does not render the provenance keys as configuration", async () => {
    stubApi();
    await renderSettings();
    await openSettled("System", "General");

    // `restart_paths` and `frozen_paths` are not settings; rendered as
    // sections they would offer the operator an edit the API cannot accept.
    expect(screen.queryByRole("heading", { name: "Frozen paths" })).toBeNull();
    expect(screen.queryByText("Restart paths")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Field descriptions" })).toBeNull();
    expect(document.body.textContent).not.toContain(
      "the worker pool is sized once, at startup",
    );
    // `version` is a computed path (see the read-only test below) and still
    // renders its value, just not as an editable field.
    expect(screen.getByText("abc123")).toBeInTheDocument();
  });

  it("picks the widget from the value's type", async () => {
    stubApi();
    await renderSettings();

    openSection("Artwork", "Badges");
    expect(screen.getByLabelText("badges.enabled")).toHaveAttribute("type", "checkbox");
    await openSettled("System", "General");
    expect(screen.getByLabelText("workers")).toHaveAttribute("type", "number");
    openSection("Servers", "Plex");
    expect(screen.getByLabelText("plex.url")).toHaveAttribute("type", "text");
    expect(screen.getByLabelText("plex.excluded_libraries[0]")).toHaveAttribute(
      "type",
      "text",
    );
  });

  it("edits a string list as a whole list at its own path", async () => {
    const fetchMock = stubApi();
    await renderSettings();
    openSection("Servers", "Plex");

    fireEvent.click(screen.getByRole("button", { name: "Add to plex.excluded_libraries" }));
    fireEvent.change(screen.getByLabelText("plex.excluded_libraries[2]"), {
      target: { value: "Anime" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Remove plex.excluded_libraries[0]" }),
    );
    await save();

    // A list is one value: the document carries the resulting list, not a
    // per-index patch the API has no way to merge.
    expect(putDocument(fetchMock)).toEqual({
      document: {
        ...SEEDED_DOCUMENT,
        plex: { ...SEEDED_DOCUMENT.plex, excluded_libraries: ["Photos", "Anime"] },
      },
    });
  });

  it("toggles a boolean into the document", async () => {
    const fetchMock = stubApi();
    await renderSettings();
    openSection("Artwork", "Badges");

    fireEvent.click(screen.getByLabelText("badges.enabled"));
    await save();

    expect(putDocument(fetchMock)).toEqual({
      document: { ...SEEDED_DOCUMENT, badges: { enabled: false } },
    });
  });

  it("clearing a number input puts the served value back", async () => {
    const fetchMock = stubApi();
    await renderSettings();
    await openSettled("System", "General");

    const field = screen.getByLabelText("workers");
    fireEvent.change(field, { target: { value: "9" } });
    expect(screen.getByRole("heading", { name: "Pending changes" })).toBeInTheDocument();

    // An empty number input is not "workers = 0", it is not "workers = null"
    // and it is not "workers back to the schema default" either -- the
    // operator emptied a box mid-edit. The row goes back to the value the
    // server served, so there is nothing pending to save.
    fireEvent.change(field, { target: { value: "" } });
    expect(screen.queryByRole("heading", { name: "Pending changes" })).toBeNull();
    expect(screen.getByLabelText("workers")).toHaveValue(5);

    fireEvent.change(field, { target: { value: "9" } });
    await save();
    expect(putDocument(fetchMock)).toEqual({
      document: { ...SEEDED_DOCUMENT, workers: 9 },
    });
  });

  it("marks a field under a frozen prefix, not only a frozen path itself", async () => {
    // `frozen_paths` keys are prefixes: `plex` freezes everything beneath it.
    // A page that only matched the key exactly would tell an operator that
    // `plex.url` applies live, which it does not.
    stubApi({
      config: {
        ...EDITOR_CONFIG,
        frozen_paths: {
          ...EDITOR_CONFIG.frozen_paths,
          plex: "the Plex client is built once at startup",
        },
      },
    });
    await renderSettings();
    openSection("Servers", "Plex");

    fireEvent.change(screen.getByLabelText("plex.url"), {
      target: { value: "http://plex:32401" },
    });

    const marker = within(rowOf(screen.getByLabelText("plex.url"))).getByText(
      "restart to apply",
    );
    expect(marker).toHaveAttribute("title", "the Plex client is built once at startup");
  });

  it("surfaces a save that failed for a reason that is not a field error", async () => {
    stubApi({ put: json({ detail: "the database is unreachable" }, 500) });
    await renderSettings();
    await openSettled("System", "General");

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await save();

    const panel = screen
      .getByRole("heading", { name: "Pending changes" })
      .closest("section") as HTMLElement;
    expect(within(panel).getByText("the database is unreachable")).toBeInTheDocument();
    // A failed save is not a save, and it must not throw the edit away either:
    // the document is still pending, still named in the diff, and still
    // saveable.
    expect(screen.queryByText(/Saved/)).toBeNull();
    expect(within(panel).getByText("workers")).toBeInTheDocument();
    expect(within(panel).getByText("5 → 9")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });
});

/** A value the server serves in a redacted form is the one thing this page
 * cannot round-trip. Seeding the document with what was served would store the
 * redaction -- `notifications.url` would become a bare host and its push token
 * would be gone -- and leaving the path out would drop the override instead.
 * The server names such paths and hands over a marker meaning "keep what is
 * stored"; these tests pin that the page uses it. */
const KEEP = "***KEEP***";

const REDACTED_CONFIG = {
  ...EDITOR_CONFIG,
  notifications: { enabled: true, url: "kuma.example.com" },
  redacted_paths: ["notifications.url"],
  keep_sentinel: KEEP,
};

describe("Settings editor, redacted values", () => {
  it("seeds a redacted override with the server's keep marker, not with what it was served", async () => {
    const fetchMock = stubApi({ config: REDACTED_CONFIG });
    await renderSettings();
    await openSettled("System", "General");

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await save();

    const body = putDocument(fetchMock);
    expect(body).toEqual({
      document: {
        ...SEEDED_DOCUMENT,
        workers: 9,
        notifications: { enabled: true, url: KEEP },
      },
    });
    // The exact failure: the served host submitted back as the setting.
    expect(JSON.stringify(body)).not.toContain("kuma.example.com");
  });

  it("shows the served redaction in the field and says what typing into it does", async () => {
    stubApi({ config: REDACTED_CONFIG });
    await renderSettings();
    openSection("Integrations", "Notifications");

    const field = screen.getByLabelText("notifications.url");
    // The marker is a state, not a value -- an operator must never be shown
    // "***KEEP***" as the current setting.
    expect(field).toHaveValue("kuma.example.com");
    expect(field).toHaveAttribute("title", REDACTED_EDIT_NOTE);
  });

  it("shows the keep marker in the pending diff, never the truncated value", async () => {
    stubApi({ config: REDACTED_CONFIG });
    await renderSettings();
    openSection("Integrations", "Notifications");

    // Replacing a redacted value is the one case where the diff has to say
    // what is being replaced, and the document holds the marker there. The
    // served rendering is a bare host with its push token already stripped,
    // so showing it as the "from" side would name a value that was never the
    // setting.
    fireEvent.change(screen.getByLabelText("notifications.url"), {
      target: { value: "https://ntfy.example.net/newToken" },
    });

    const diff = document.querySelector(".config-diff") as HTMLElement;
    expect(diff.textContent).toContain(KEEP);
    expect(diff.textContent).not.toContain("kuma.example.com");
  });

  it("replaces the keep marker with what is typed into the field", async () => {
    const fetchMock = stubApi({ config: REDACTED_CONFIG });
    await renderSettings();
    openSection("Integrations", "Notifications");

    const url = "https://kuma.example.com/api/push/newToken";
    fireEvent.change(screen.getByLabelText("notifications.url"), {
      target: { value: url },
    });
    expect(screen.getByLabelText("notifications.url")).toHaveValue(url);
    await save();

    const body = putDocument(fetchMock);
    expect(body).toEqual({
      document: { ...SEEDED_DOCUMENT, notifications: { enabled: true, url } },
    });
    expect(JSON.stringify(body)).not.toContain(KEEP);
  });

  it("falls back to the served value when the server names no marker", async () => {
    // A response that lists redacted paths but no marker cannot be seeded
    // either way, so the page behaves as it did before the marker existed
    // rather than inventing one.
    const fetchMock = stubApi({
      config: { ...REDACTED_CONFIG, keep_sentinel: undefined },
    });
    await renderSettings();
    await openSettled("System", "General");

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await save();

    expect(putDocument(fetchMock)).toEqual({
      document: {
        ...SEEDED_DOCUMENT,
        workers: 9,
        notifications: { enabled: true, url: "kuma.example.com" },
      },
    });
  });

  it("does not render the redaction contract as configuration", async () => {
    stubApi({ config: REDACTED_CONFIG });
    await renderSettings();
    await openSettled("System", "General");

    expect(screen.queryByLabelText("keep_sentinel")).toBeNull();
    expect(screen.queryByText("Redacted paths")).toBeNull();
    expect(screen.queryByText("Keep sentinel")).toBeNull();
  });
});

/** Preview and the two ways to commit.
 *
 * Routed by *path* as well as by method, unlike `stubApi`: preview, apply and
 * save are three different endpoints and a stub that answered any non-GET
 * with one body would pass against a page that posted the pending document
 * to whichever of them it liked. An unstubbed call is an error rather than a
 * default, which is what makes the endpoint-swap mutation go red instead of
 * quietly succeeding against the other handler's stub.
 */
function stubEditor({
  config = EDITOR_CONFIG as unknown,
  responses = {},
}: { config?: unknown; responses?: Record<string, Response> } = {}) {
  const fetchMock = vi.fn((input: string, init?: RequestInit) => {
    if ((init?.method ?? "GET") === "GET") return Promise.resolve(json(config));
    const stubbed = responses[input];
    if (stubbed === undefined) {
      return Promise.reject(new Error(`unstubbed ${init?.method} ${input}`));
    }
    return Promise.resolve(stubbed.clone());
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The one call the page made to `path`, with its method and parsed body. */
function callTo(
  fetchMock: ReturnType<typeof stubEditor>,
  path: string,
): { method: string; body: unknown } {
  const calls = fetchMock.mock.calls.filter(([input]) => input === path);
  expect(calls).toHaveLength(1);
  return {
    method: String(calls[0][1]?.method),
    body: JSON.parse(String(calls[0][1]?.body)),
  };
}

function previewBody(impact: unknown, versionAfter = "def456") {
  return json({
    version_before: "abc123",
    version_after: versionAfter,
    restart_required: [],
    impact,
  });
}

async function click(name: string) {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name }));
  });
}

/** The pending panel, which is where every action and every result lives. */
function pendingPanel(): HTMLElement {
  return screen
    .getByRole("heading", { name: "Pending changes" })
    .closest("section") as HTMLElement;
}

describe("Settings preview", () => {
  it("previews the pending document and reports the count with the breakdown", async () => {
    // `affected` below the population is what a `skip_tba` edit looks like:
    // it gates title cards without moving the render version, so it is the
    // only edit that can discriminate between art kinds.
    const fetchMock = stubEditor({
      responses: {
        "/api/config/preview": previewBody({
          affected: 3,
          by_art_kind: { poster: 2, title_card: 1 },
          of_total: 9,
        }),
      },
    });
    await renderSettings();
    openSection("Artwork", "Artwork");

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await click("Preview impact");

    const call = callTo(fetchMock, "/api/config/preview");
    expect(call.method).toBe("POST");
    expect(call.body).toEqual({
      document: {
        ...SEEDED_DOCUMENT,
        artwork: { poster: { text: { min_point_size: 24 } } },
      },
    });

    // "~", never "3": the walk cannot know which renders composited a logo,
    // so it overcounts by construction.
    const panel = pendingPanel();
    expect(
      within(panel).getByText(/~3 of 9 artwork renders are out of date/),
    ).toBeInTheDocument();
    expect(within(panel).getByText("poster: 2")).toBeInTheDocument();
    expect(within(panel).getByText("title_card: 1")).toBeInTheDocument();
  });

  it("names the kinds a gated breakdown says the edit touched", async () => {
    // `affected` below `of_total` happens through a gate (a disabled kind, or
    // `skip_tba`) or, since row 111, through an edit that simply reached
    // fewer kinds than were examined. Both make the breakdown a real signal.
    stubEditor({
      responses: {
        "/api/config/preview": previewBody({
          affected: 3,
          by_art_kind: { poster: 2, title_card: 1 },
          of_total: 9,
        }),
      },
    });
    await renderSettings();
    openSection("Artwork", "Artwork");

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await click("Preview impact");

    const panel = pendingPanel();
    expect(
      within(panel).getByText(
        /a kind missing from the breakdown was excluded by a gate/i,
      ),
    ).toBeInTheDocument();
    expect(
      within(panel).getByText(
        /a kind counted below is one whose stored fingerprint this edit moves/i,
      ),
    ).toBeInTheDocument();
  });

  it("carries the API's own approximation caveat, verbatim, as the count's tooltip", async () => {
    stubEditor({
      responses: {
        "/api/config/preview": previewBody({
          affected: 3,
          by_art_kind: { poster: 3 },
          of_total: 9,
        }),
      },
    });
    await renderSettings();
    openSection("Artwork", "Artwork");

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await click("Preview impact");

    expect(
      within(pendingPanel()).getByText(/~3 of 9 artwork renders are out of date/),
    ).toHaveAttribute("title", IMPACT_CAVEAT);
    // The caveat is the docstring of src/autoposter/config/impact.py, not a
    // paraphrase of it: the direction of the error is the whole point, and a
    // reworded version is how "overcount, never an undercount" quietly
    // becomes "roughly".
    expect(IMPACT_CAVEAT).toBe(
      "The consequence is honest and one-directional: a poster whose real " +
        "render composited a logo will not match the recomputed value, so it " +
        "is reported as affected whatever the edit was. That is an overcount, " +
        "never an undercount, of the text and version changes the operator is " +
        "actually asking about.",
    );
  });

  it("says an every-kind count is the edit reaching every kind, not the artwork section being one hash", async () => {
    // Before row 111 `affected === of_total` was what EVERY artwork edit
    // looked like, and the copy said so. Now it means a genuinely shared
    // input -- an asset root, use_original_title, output_quality -- and the
    // sentence must not go back to blaming the artwork section.
    stubEditor({
      responses: {
        "/api/config/preview": previewBody({
          affected: 9,
          by_art_kind: { poster: 5, title_card: 4 },
          of_total: 9,
        }),
      },
    });
    await renderSettings();
    openSection("Artwork", "Artwork");

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await click("Preview impact");

    const panel = pendingPanel();
    expect(
      within(panel).getByText(/this edit reaches every examined row — ~9 of 9 artwork renders/i),
    ).toBeInTheDocument();
    expect(panel.textContent).not.toMatch(/re-renders the whole library/i);
    expect(
      within(panel).getByText(
        /a kind counted below is one whose stored fingerprint this edit moves/i,
      ),
    ).toBeInTheDocument();
  });

  it("no longer claims a counted kind is not one the edit singled out", async () => {
    // The published promise row 111 inverts. Asserted as an ABSENCE, and on
    // the whole-library fixture -- the arm where the old sentence used to be
    // rendered -- so restoring the constant anywhere turns this red rather
    // than passing vacuously on a fixture that never showed it.
    stubEditor({
      responses: {
        "/api/config/preview": previewBody({
          affected: 9,
          by_art_kind: { poster: 5, title_card: 4 },
          of_total: 9,
        }),
      },
    });
    await renderSettings();
    openSection("Artwork", "Artwork");

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await click("Preview impact");

    const panel = pendingPanel();
    expect(panel.textContent).not.toMatch(/singled out/i);
    expect(panel.textContent).not.toMatch(/covers the whole artwork section/i);
  });

  it("carries the per-kind note in the partial arm too", async () => {
    // The note is true in BOTH arms now, which is the whole inversion: it
    // used to be rendered only where `affected === of_total`, because that
    // was the only case it described.
    stubEditor({
      responses: {
        "/api/config/preview": previewBody({
          affected: 1,
          by_art_kind: { title_card: 1 },
          of_total: 5,
        }),
      },
    });
    await renderSettings();
    openSection("Artwork", "Artwork");

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await click("Preview impact");

    const panel = pendingPanel();
    expect(within(panel).getByText(/~1 of 5 artwork renders are out of date/)).toBeInTheDocument();
    expect(
      within(panel).getByText(
        /a kind counted below is one whose stored fingerprint this edit moves/i,
      ),
    ).toBeInTheDocument();
  });

  it("renders a null impact as no re-renders at all, with no count", async () => {
    // A scheduler tweak cannot change a rendered image, and the server says so
    // by sending null rather than a number made entirely of the approximation.
    stubEditor({
      responses: {
        "/api/config/preview": previewBody(null, "abc123"),
      },
    });
    await renderSettings();
    await openSettled("System", "General");

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await click("Preview impact");

    const panel = pendingPanel();
    expect(
      within(panel).getByText(
        "No re-renders — this change does not affect rendered artwork.",
      ),
    ).toBeInTheDocument();
    // Nothing that reads as a count: no "~", no "of N artwork renders".
    expect(panel.textContent).not.toContain("~");
    expect(panel.textContent).not.toMatch(/artwork renders/);
  });

  it("clears a stale save result when a fresh preview starts", async () => {
    // A "Saved..." panel from an earlier commit sitting beside a preview for
    // a different, later document reads as though that save already
    // accounted for what the preview is about to show.
    stubEditor({
      responses: {
        "/api/config/overrides": json({
          version_before: "abc123",
          version_after: "def456",
          restart_required: [],
        }),
        "/api/config/preview": previewBody(null, "def456"),
      },
    });
    await renderSettings();
    await openSettled("System", "General");

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await save();
    expect(screen.getByText(/abc123 → def456/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "12" } });
    await click("Preview impact");

    expect(screen.queryByText(/abc123 → def456/)).toBeNull();
  });

  it("shows the restart list a preview response carries, before anything is committed", async () => {
    stubEditor({
      responses: {
        "/api/config/preview": json({
          version_before: "abc123",
          version_after: "def456",
          restart_required: ["workers"],
          impact: null,
        }),
      },
    });
    await renderSettings();
    await openSettled("System", "General");

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await click("Preview impact");

    expect(
      within(pendingPanel()).getByText(/Restart required to apply: workers/),
    ).toBeInTheDocument();
  });

  it("shows the inert list a preview response carries, separately from restart_required", async () => {
    stubEditor({
      responses: {
        "/api/config/preview": json({
          version_before: "abc123",
          version_after: "abc123",
          restart_required: [],
          inert: ["api_docs_enabled"],
          impact: null,
        }),
      },
    });
    await renderSettings();
    await openSettled("System", "General");

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await click("Preview impact");

    const panel = pendingPanel();
    expect(
      within(panel).getByText(
        "Takes effect at the next restart: api_docs_enabled",
      ),
    ).toBeInTheDocument();
    expect(within(panel).queryByText(/Restart required to apply/)).toBeNull();
  });

  it("previews with the keep sentinel, exactly as a save does", async () => {
    const fetchMock = stubEditor({
      config: REDACTED_CONFIG,
      responses: { "/api/config/preview": previewBody(null, "abc123") },
    });
    await renderSettings();
    await openSettled("System", "General");

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await click("Preview impact");

    const body = callTo(fetchMock, "/api/config/preview").body;
    expect(body).toEqual({
      document: {
        ...SEEDED_DOCUMENT,
        workers: 9,
        notifications: { enabled: true, url: KEEP },
      },
    });
    expect(JSON.stringify(body)).not.toContain("kuma.example.com");
  });

  it("surfaces a failed preview inline and keeps the edit", async () => {
    stubEditor({
      responses: {
        "/api/config/preview": json({ detail: "the database is unreachable" }, 500),
      },
    });
    await renderSettings();
    await openSettled("System", "General");

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await click("Preview impact");

    const panel = pendingPanel();
    expect(within(panel).getByText("the database is unreachable")).toBeInTheDocument();
    expect(within(panel).getByText("workers")).toBeInTheDocument();
    expect(within(panel).getByText("5 → 9")).toBeInTheDocument();
  });

  it("lands a preview's 422 inline at the field its path names", async () => {
    stubEditor({
      responses: {
        "/api/config/preview": json(
          { detail: [{ path: "plex.url", message: "Input should be a valid URL" }] },
          422,
        ),
      },
    });
    await renderSettings();
    openSection("Servers", "Plex");

    fireEvent.change(screen.getByLabelText("plex.url"), {
      target: { value: "not a url" },
    });
    await click("Preview impact");

    expect(
      within(rowOf(screen.getByLabelText("plex.url"))).getByText(
        "Input should be a valid URL",
      ),
    ).toBeInTheDocument();
  });

  it("counts the collection posters an edit would re-composite, beside a null impact", async () => {
    // The row-105 case exactly: `collections` is excluded from render_version,
    // so the server reports no re-renders AND a real collection-poster cost.
    // Both sentences are true and both have to be on the page.
    stubEditor({
      responses: {
        "/api/config/preview": json({
          version_before: "abc123",
          version_after: "abc123",
          restart_required: [],
          impact: null,
          collection_posters: 41,
        }),
      },
    });
    await renderSettings();
    openSection("Artwork", "Artwork");

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await click("Preview impact");

    const panel = pendingPanel();
    expect(
      within(panel).getByText(/No re-renders/),
    ).toBeInTheDocument();
    // "~", never "41": a poster the operator placed themselves passes through
    // untouched and the server cannot know which those are.
    expect(
      within(panel).getByText(/~41 managed collection posters/),
    ).toBeInTheDocument();
  });

  it("says nothing about collection posters when there are none to re-composite", async () => {
    stubEditor({
      responses: { "/api/config/preview": previewBody(null, "abc123") },
    });
    await renderSettings();
    openSection("Artwork", "Artwork");

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await click("Preview impact");

    expect(
      within(pendingPanel()).queryByText(/managed collection posters/),
    ).not.toBeInTheDocument();
  });
});

describe("Settings apply", () => {
  it("applies through /api/config/apply and reports what it queued", async () => {
    const fetchMock = stubEditor({
      responses: {
        "/api/config/apply": json({
          version_before: "abc123",
          version_after: "def456",
          restart_required: [],
          queued: 7,
          skipped: 2,
        }),
      },
    });
    await renderSettings();
    openSection("Artwork", "Artwork");

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await click("Save and re-render");

    const call = callTo(fetchMock, "/api/config/apply");
    expect(call.method).toBe("POST");
    expect(call.body).toEqual({
      document: {
        ...SEEDED_DOCUMENT,
        artwork: { poster: { text: { min_point_size: 24 } } },
      },
    });
    // Apply is the save plus an enqueue, so both halves are reported.
    expect(screen.getByText(/abc123 → def456/)).toBeInTheDocument();
    expect(screen.getByText(/Queued 7 items to re-render/)).toBeInTheDocument();
    // `skipped` is the pending-dedupe arbiter's count, not a failure.
    expect(screen.getByText(/2 already queued/)).toBeInTheDocument();
  });

  it("applies with the keep sentinel, exactly as a save does", async () => {
    const fetchMock = stubEditor({
      config: REDACTED_CONFIG,
      responses: {
        "/api/config/apply": json({
          version_before: "abc123",
          version_after: "def456",
          restart_required: [],
          queued: 1,
          skipped: 0,
        }),
      },
    });
    await renderSettings();
    await openSettled("System", "General");

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await click("Save and re-render");

    const body = callTo(fetchMock, "/api/config/apply").body;
    expect(body).toEqual({
      document: {
        ...SEEDED_DOCUMENT,
        workers: 9,
        notifications: { enabled: true, url: KEEP },
      },
    });
    expect(JSON.stringify(body)).not.toContain("kuma.example.com");
  });

  it("saves only through the PUT, queueing nothing", async () => {
    const fetchMock = stubEditor({
      responses: {
        "/api/config/overrides": json({
          version_before: "abc123",
          version_after: "def456",
          restart_required: [],
        }),
      },
    });
    await renderSettings();
    openSection("Artwork", "Artwork");

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await save();

    const call = callTo(fetchMock, "/api/config/overrides");
    expect(call.method).toBe("PUT");
    expect(call.body).toEqual({
      document: {
        ...SEEDED_DOCUMENT,
        artwork: { poster: { text: { min_point_size: 24 } } },
      },
    });
    // The regenerate-later arm: it must not have touched the apply endpoint,
    // and it must not claim to have queued anything.
    expect(
      fetchMock.mock.calls.filter(([input]) => input === "/api/config/apply"),
    ).toHaveLength(0);
    expect(screen.queryByText(/Queued/)).toBeNull();
  });

  it("discards the preview once a save actually commits it", async () => {
    // A successful save answers the question the preview was asking -- the
    // document is stored now -- so a preview left standing would be a count
    // that describes nothing.
    stubEditor({
      responses: {
        "/api/config/preview": previewBody({
          affected: 9,
          by_art_kind: { poster: 9 },
          of_total: 9,
        }),
        "/api/config/overrides": json({
          version_before: "abc123",
          version_after: "def456",
          restart_required: [],
        }),
      },
    });
    await renderSettings();
    openSection("Artwork", "Artwork");

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await click("Preview impact");
    expect(
      within(pendingPanel()).getByText(/~9 of 9 artwork renders/),
    ).toBeInTheDocument();

    await save();
    // The GET this save triggers re-serves the unedited config, so the page
    // goes clean and the pending panel -- and the preview inside it --
    // disappears.
    expect(screen.queryByRole("heading", { name: "Pending changes" })).toBeNull();

    // Make the document dirty again without previewing it. If the preview
    // had merely been hidden by the panel going away rather than actually
    // discarded, its stale count would reappear here.
    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    expect(
      within(pendingPanel()).queryByText(/artwork renders/),
    ).toBeNull();
  });

  it("says what happens to the artwork after a save-only", async () => {
    stubEditor({
      responses: {
        "/api/config/overrides": json({
          version_before: "abc123",
          version_after: "def456",
          restart_required: [],
        }),
      },
    });
    await renderSettings();
    await openSettled("System", "General");

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });

    // Save leaves stale fingerprints on disk on purpose; an operator who is
    // not told that reads "Saved" as "done".
    expect(
      within(pendingPanel()).getByText(/drift sweep and the full pass pick/i),
    ).toBeInTheDocument();
  });

  it("disables every action while one is in flight", async () => {
    let release: (response: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn((_input: string, init?: RequestInit) => {
        if ((init?.method ?? "GET") === "GET") {
          return Promise.resolve(json(EDITOR_CONFIG));
        }
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      }),
    );
    await renderSettings();
    await openSettled("System", "General");

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview impact" }));

    // Discard is on the same bar and is disabled with them: throwing the edit
    // away while a save is in flight would leave the page unable to say what
    // the save it is waiting on was about.
    const actions = ["Discard", "Preview impact", "Save", "Save and re-render"];
    for (const name of actions) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }

    await act(async () => {
      release(previewBody(null, "abc123"));
    });
    for (const name of actions) {
      expect(screen.getByRole("button", { name })).toBeEnabled();
    }
  });

  it("drops a preview that a further edit has made stale", async () => {
    stubEditor({
      responses: {
        "/api/config/preview": previewBody({
          affected: 9,
          by_art_kind: { poster: 9 },
          of_total: 9,
        }),
      },
    });
    await renderSettings();
    openSection("Artwork", "Artwork");

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await click("Preview impact");
    expect(
      within(pendingPanel()).getByText(/~9 of 9 artwork renders/),
    ).toBeInTheDocument();

    // The count answered a question about a document that no longer exists.
    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "25" },
    });
    expect(within(pendingPanel()).queryByText(/~9 of 9 artwork renders/)).toBeNull();
  });
});

describe("Settings stale-save recovery", () => {
  const SEEDED = { ...EDITOR_CONFIG, overrides_revision: "rev-1" };

  it("sends the revision it seeded from with every write and every preview", async () => {
    // Clause 8 of the frontend's contract. Without it this page can still
    // delete what CatalogPanel, GroupsPanel or a second tab just saved.
    const fetchMock = stubEditor({
      config: SEEDED,
      responses: {
        "/api/config/overrides": json({
          version_before: "abc123",
          version_after: "def456",
          restart_required: [],
          overrides_revision: "rev-2",
        }),
        "/api/config/preview": previewBody(null),
      },
    });
    await renderSettings();
    await openSettled("System", "General");

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await click("Preview impact");
    expect(callTo(fetchMock, "/api/config/preview").body).toEqual({
      document: { ...SEEDED_DOCUMENT, workers: 9 },
      expected_revision: "rev-1",
    });

    await save();
    expect(callTo(fetchMock, "/api/config/overrides").body).toEqual({
      document: { ...SEEDED_DOCUMENT, workers: 9 },
      expected_revision: "rev-1",
    });
  });

  it("tells the operator and re-seeds when the server refuses a stale save", async () => {
    // The second GET serves the state another page wrote, which is what the
    // page must end up showing -- not the operator's discarded edit.
    const moved = {
      ...EDITOR_CONFIG,
      workers: 5,
      badges: { enabled: false },
      overrides_revision: "rev-9",
    };
    let served: unknown = SEEDED;
    const fetchMock = vi.fn((_input: string, init?: RequestInit) => {
      if ((init?.method ?? "GET") === "GET") return Promise.resolve(json(served));
      served = moved;
      return Promise.resolve(
        json(
          {
            message: "these settings changed somewhere else",
            current_revision: "rev-9",
            changed_paths: ["badges.enabled"],
          },
          409,
        ),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    await renderSettings();
    await openSettled("System", "General");

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await save();

    // Said, not swallowed -- and said outside the pending panel, which the
    // re-seed makes disappear.
    expect(screen.getByText(STALE_SAVE_NOTE)).toBeInTheDocument();
    // Re-seeded from the server, not from what was typed.
    expect(screen.getByLabelText("workers")).toHaveValue(5);
    // And NOT retried: exactly one write left this page.
    expect(
      fetchMock.mock.calls.filter(([, init]) => (init?.method ?? "GET") !== "GET"),
    ).toHaveLength(1);
  });

  it("carries the new revision into the next save after a successful one", async () => {
    // Otherwise the page's second save is stale against its own first, and
    // every page would 409 itself on the second click.
    const after = { ...EDITOR_CONFIG, overrides_revision: "rev-2" };
    let served: unknown = SEEDED;
    const fetchMock = vi.fn((_input: string, init?: RequestInit) => {
      if ((init?.method ?? "GET") === "GET") return Promise.resolve(json(served));
      served = after;
      return Promise.resolve(
        json({
          version_before: "abc123",
          version_after: "def456",
          restart_required: [],
          overrides_revision: "rev-2",
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    await renderSettings();
    await openSettled("System", "General");

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await save();
    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "11" } });
    await save();

    const writes = fetchMock.mock.calls.filter(
      ([, init]) => (init?.method ?? "GET") !== "GET",
    );
    expect(JSON.parse(String(writes[1][1]?.body)).expected_revision).toBe("rev-2");
  });
});

/** The tabbed page itself: one tab's sections at a time, collapsed until
 * asked for, and one pending change shared across all seven. */
describe("Settings tabs", () => {
  const FULL = {
    ...CONFIG,
    scheduler: { poll_seconds: 5 },
    radarr: { enabled: false, base_url: "" },
    collections: { enabled: true },
    operations: { write_to_plex: true },
    overrides_revision: "r1",
    restart_paths: [],
    frozen_paths: {
      workers: "the worker pool is sized once at startup",
      scheduler: "the poll interval is read once at startup",
    },
    live_paths: [],
    computed_paths: [],
    redacted_paths: [],
    keep_sentinel: "***KEEP***",
    field_descriptions: {},
  };

  /** A deployment the per-library matrix can actually render: it takes its
   * columns from `collections.libraries` and its rows from the
   * `libraries.{}.` wildcard in `field_descriptions`. `upload_to_plex` is
   * deliberately outside that wildcard, so the branch survives clearing the
   * one cell the matrix does offer. */
  const LIBRARIES = {
    ...CONFIG,
    collections: { libraries: ["Movies", "Shows"] },
    libraries: { Movies: { badges: { enabled: false, upload_to_plex: true } } },
    field_descriptions: {
      "libraries.{}.badges.enabled": "Whether this library gets badges.",
    },
  };

  it("renders the seven tabs and shows one tab's sections at a time", async () => {
    stubConfig(FULL);
    await renderSettings();
    for (const label of [
      "Servers",
      "Libraries",
      "Artwork",
      "Collections",
      "Metadata",
      "Integrations",
      "System",
    ]) {
      expect(screen.getByRole("tab", { name: label })).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole("tab", { name: "Integrations" }));
    expect(screen.getByRole("button", { name: "Radarr" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Collections" })).not.toBeInTheDocument();
  });

  it("collapses every section until one is opened", async () => {
    stubConfig(FULL);
    await renderSettings();
    await openSettled("System");
    expect(screen.getByRole("button", { name: "Scheduler" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    fireEvent.click(screen.getByRole("button", { name: "Scheduler" }));
    expect(screen.getByLabelText("scheduler.poll_seconds")).toBeInTheDocument();
  });

  it("carries the restart pill on a frozen section's header", async () => {
    stubConfig(FULL);
    await renderSettings();
    await openSettled("System");

    // The section's own pill, from `frozen_paths`, and there before anything
    // is edited -- unlike a row's pill, which appears only once that row is
    // pending.
    const pill = screen.getByText("restart to apply");
    expect(pill).toHaveAttribute(
      "title",
      "the poll interval is read once at startup",
    );
    // Only the section the server named. `workers` is frozen too, but it is a
    // top-level scalar gathered into General with unrelated others, and a
    // reason pinned to that header would be wrong for every row it did not
    // come from.
    expect(screen.getAllByText("restart to apply")).toHaveLength(1);
  });

  it("names the tabs holding pending edits in the sticky bar", async () => {
    stubConfig(FULL);
    await renderSettings();
    await openSettled("System", "Scheduler");
    fireEvent.change(screen.getByLabelText("scheduler.poll_seconds"), {
      target: { value: "9" },
    });
    expect(screen.getByText(/Unsaved changes on: System/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Discard" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview impact" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save and re-render" }),
    ).toBeInTheDocument();
  });

  it("names every tab an edit is waiting on, not just the one in front", async () => {
    // The whole reason the bar names tabs: an operator who edited two and
    // walked away from one has no other way to be told which.
    stubConfig(FULL);
    await renderSettings();
    await openSettled("System", "Scheduler");
    fireEvent.change(screen.getByLabelText("scheduler.poll_seconds"), {
      target: { value: "9" },
    });
    openSection("Integrations", "Radarr");
    fireEvent.click(screen.getByLabelText("radarr.enabled"));

    // In TABS order, which is the order they are on screen in.
    expect(
      screen.getByText("Unsaved changes on: Integrations, System"),
    ).toBeInTheDocument();
  });

  it("shows the changed paths rather than the whole configuration", async () => {
    // The document is the whole store now, so the panel that used to dump it
    // would bury one edit in every setting the service has.
    stubConfig(FULL);
    await renderSettings();
    await openSettled("System", "Scheduler");
    fireEvent.change(screen.getByLabelText("scheduler.poll_seconds"), {
      target: { value: "9" },
    });

    const panel = screen
      .getByRole("heading", { name: "Pending changes" })
      .closest("section") as HTMLElement;
    expect(within(panel).getByText("scheduler.poll_seconds")).toBeInTheDocument();
    expect(within(panel).getByText("5 → 9")).toBeInTheDocument();
    // Untouched settings stay out of it entirely.
    expect(within(panel).queryByText(/workers/)).toBeNull();
    expect(within(panel).queryByText(/plex/)).toBeNull();
  });

  it("discards the pending change and the bar goes with it", async () => {
    stubConfig(FULL);
    await renderSettings();
    await openSettled("System", "Scheduler");
    fireEvent.change(screen.getByLabelText("scheduler.poll_seconds"), {
      target: { value: "9" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(screen.queryByText(/Unsaved changes on:/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("scheduler.poll_seconds")).toHaveValue(5);
  });

  it("remembers the open section on a tab after a round trip to another", async () => {
    stubConfig(FULL);
    await renderSettings();
    openSection("Integrations", "Radarr");
    expect(screen.getByRole("button", { name: "Radarr" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );

    // Away and back. The open section is remembered per tab, so the one
    // opened here survives being off screen -- and the tab visited in between
    // does not inherit it.
    await openSettled("System");
    expect(screen.getByRole("button", { name: "Scheduler" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    openSection("Integrations");
    expect(screen.getByRole("button", { name: "Radarr" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("discards the preview and the save error along with the edit", async () => {
    stubEditor({
      config: FULL,
      responses: {
        "/api/config/preview": json({
          version_before: "a",
          version_after: "b",
          restart_required: [],
          impact: null,
        }),
        "/api/config/overrides": json({ detail: "the database is unreachable" }, 500),
      },
    });
    await renderSettings();
    await openSettled("System", "Scheduler");
    fireEvent.change(screen.getByLabelText("scheduler.poll_seconds"), {
      target: { value: "9" },
    });

    await click("Preview impact");
    expect(screen.getByText(/No re-renders/)).toBeInTheDocument();
    await save();
    expect(screen.getByText("the database is unreachable")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    // Both go with the edit: the count answered a question about a document
    // that no longer exists, and the refusal was reported against it.
    expect(screen.queryByText(/No re-renders/)).toBeNull();
    expect(screen.queryByText("the database is unreachable")).toBeNull();
    expect(screen.getByLabelText("scheduler.poll_seconds")).toHaveValue(5);

    // Discarded rather than merely hidden by the panel going away with the
    // dirty flag: make the document pending again and neither comes back.
    fireEvent.change(screen.getByLabelText("scheduler.poll_seconds"), {
      target: { value: "11" },
    });
    expect(screen.getByText(/Unsaved changes on: System/)).toBeInTheDocument();
    expect(screen.queryByText(/No re-renders/)).toBeNull();
    expect(screen.queryByText("the database is unreachable")).toBeNull();
  });

  it("renders the per-library matrix on the Libraries tab and nowhere else", async () => {
    stubConfig(LIBRARIES);
    await renderSettings();
    openSection("Libraries");

    expect(
      screen.getByRole("heading", { name: "Per-library overrides" }),
    ).toBeInTheDocument();
    // The generic tree for the same section sits under it. The matrix reports
    // a per-library list or mapping cell it cannot edit, and the tree is
    // where that edit lives -- so both belong on this tab, together.
    expect(screen.getByRole("button", { name: "Libraries" })).toBeInTheDocument();

    for (const tab of [
      "Servers",
      "Artwork",
      "Collections",
      "Metadata",
      "Integrations",
      "System",
    ]) {
      await openSettled(tab);
      expect(
        screen.queryByRole("heading", { name: "Per-library overrides" }),
      ).toBeNull();
    }
  });

  it("renders a cleared cell as a removal in the diff", async () => {
    // Clearing a cell deletes the key rather than writing today's global into
    // it -- that is the whole of the matrix's "inherit" -- so the diff has to
    // be able to say a path went away, not just that it changed.
    stubConfig(LIBRARIES);
    await renderSettings();
    openSection("Libraries");

    fireEvent.change(screen.getByLabelText("libraries.Movies.badges.enabled"), {
      target: { value: "inherit" },
    });

    const panel = screen
      .getByRole("heading", { name: "Pending changes" })
      .closest("section") as HTMLElement;
    expect(
      within(panel).getByText("libraries.Movies.badges.enabled"),
    ).toBeInTheDocument();
    expect(within(panel).getByText("off → (not set)")).toBeInTheDocument();
  });

  it("sends the whole document, not a delta, when saving", async () => {
    const calls: RequestInit[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
        if (init?.method !== undefined) calls.push(init);
        return Promise.resolve(
          new Response(
            JSON.stringify(
              init?.method === undefined
                ? FULL
                : { version_before: "a", version_after: "b", restart_required: [] },
            ),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }),
    );
    await renderSettings();
    await openSettled("System", "Scheduler");
    fireEvent.change(screen.getByLabelText("scheduler.poll_seconds"), {
      target: { value: "9" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
    });
    const body = JSON.parse(String(calls[0].body));
    expect(body.document.scheduler.poll_seconds).toBe(9);
    expect(body.document.collections).toEqual({ enabled: true });
    expect(body.expected_revision).toBe("r1");
    expect(body.document.secrets).toBeUndefined();
  });
});
