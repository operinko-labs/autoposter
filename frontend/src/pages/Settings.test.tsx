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
import { REDACTED_EDIT_NOTE, Settings, TMDB_NOTICE } from "./Settings";

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
});

/** The config fetch resolves on the next microtask, so rendering without
 * awaiting leaves a state update outside act(). */
async function renderSettings() {
  await act(async () => {
    render(<Settings />);
  });
}

describe("Settings attribution", () => {
  it("renders TMDB's notice with their required wording, exactly", async () => {
    stubConfig();
    await renderSettings();

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

    const link = screen.getByRole("link", { name: /TheTVDB/i }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("https://thetvdb.com");
  });
});

describe("Settings configuration", () => {
  it("renders titled sections with rows, not a JSON dump", async () => {
    stubConfig();
    await renderSettings();

    // Top-level objects become titled panels...
    expect(screen.getByRole("heading", { name: "Plex" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Badges" })).toBeInTheDocument();
    // ...nested objects become subsections, recursively (artwork -> poster ->
    // text), with snake_case keys turned into words.
    expect(screen.getByRole("heading", { name: "Poster" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Text" })).toBeInTheDocument();
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
    expect(screen.getByLabelText("artwork.poster.text.font")).toHaveValue(
      "Comfortaa-Medium.ttf",
    );
    // And the raw JSON <pre> dump is gone.
    expect(document.querySelector("pre")).toBeNull();
    expect(document.body.textContent).not.toContain("{");
  });

  it("renders redactions as a badge, never the marker string", async () => {
    stubConfig();
    await renderSettings();

    // Both secrets render the badge...
    expect(screen.getAllByText("redacted")).toHaveLength(2);
    // ...and the literal marker never reaches the page as a value.
    expect(document.body.textContent).not.toContain(REDACTED);
  });

  it("renders booleans as toggles reflecting their state", async () => {
    stubConfig();
    await renderSettings();

    // Was: on/off pills. The editor makes them toggles, so the state that
    // used to be a word is now the checkbox's own checked-ness.
    expect(screen.getByLabelText("badges.enabled")).toBeChecked();
    expect(screen.getByLabelText("artwork.use_logo")).toBeChecked();
    expect(screen.getByLabelText("badges.upload_to_plex")).not.toBeChecked();
    expect(screen.getByLabelText("notifications.enabled")).not.toBeChecked();
  });

  it("renders an empty string as an empty text field, not as (not set)", async () => {
    stubConfig();
    await renderSettings();

    // An empty string is a value the operator can type into; only a genuine
    // null (which the API refuses as an override) stays a read-only marker.
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
    // licence condition, not a view of server data.
    expect(screen.getByAltText("TMDB")).toBeInTheDocument();
  });
});

/** The editor's own fixture: it carries the two provenance keys the enriched
 * GET adds, one frozen prefix with the server's reason, and one field of each
 * editable value type. */
const EDITOR_CONFIG = {
  version: "abc123",
  workers: 5,
  plex: { url: "http://plex:32400", excluded_libraries: ["Muskarit", "Photos"] },
  badges: { enabled: true },
  artwork: { poster: { text: { min_point_size: 20 } } },
  secrets: { plex_token: REDACTED },
  overridden_paths: [] as string[],
  frozen_paths: { workers: "the worker pool is sized once, at startup" },
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
  it("sends a document carrying only the touched path", async () => {
    const fetchMock = stubApi();
    await renderSettings();

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await save();

    // The overrides document is a delta, not a round-trip of the running
    // config: everything present in it is something the operator changed.
    // A builder that sent the whole config would store `workers`, `plex` and
    // the rest as overrides, freezing today's values against every future
    // change to the mounted YAML.
    expect(putDocument(fetchMock)).toEqual({
      document: { artwork: { poster: { text: { min_point_size: 24 } } } },
    });
  });

  it("reports the version move and the restart list the save returned", async () => {
    stubApi({
      put: json({
        version_before: "abc123",
        version_after: "def456",
        restart_required: ["workers"],
      }),
    });
    await renderSettings();

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await save();

    expect(screen.getByText(/abc123 → def456/)).toBeInTheDocument();
    expect(screen.getByText(/Restart required to apply: workers/)).toBeInTheDocument();
  });

  it("lands a 422 inline at the field its path names", async () => {
    stubApi({
      put: json(
        { detail: [{ path: "plex.url", message: "Input should be a valid URL" }] },
        422,
      ),
    });
    await renderSettings();

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

    // Unedited, the marker would be noise: nothing is pending.
    expect(screen.queryByText("restart to apply")).toBeNull();

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });

    const marker = within(rowOf(screen.getByLabelText("workers"))).getByText(
      "restart to apply",
    );
    expect(marker).toHaveAttribute("title", "the worker pool is sized once, at startup");
    // A live path stays unmarked -- a page that marked everything would be
    // telling the operator to restart for a change that already took effect.
    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    expect(
      within(rowOf(screen.getByLabelText("artwork.poster.text.min_point_size")))
        .queryByText("restart to apply"),
    ).toBeNull();
  });

  it("badges an overridden field and clears it by omission, never by null", async () => {
    const overridden = { ...EDITOR_CONFIG, workers: 9, overridden_paths: ["workers"] };
    const fetchMock = stubApi({ config: [overridden, EDITOR_CONFIG] });
    await renderSettings();

    const badge = within(rowOf(screen.getByLabelText("workers"))).getByText("overridden");
    expect(badge).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Clear override for workers" }));
    await save();

    // Reverting to the mounted file's value is expressed by the key being
    // absent. Writing null asks the API for a null-valued setting, which it
    // rejects -- so a null here is a clear control that cannot clear.
    const body = putDocument(fetchMock);
    expect(body).toEqual({ document: {} });
    expect(JSON.stringify(body)).not.toContain("null");

    // The page re-reads after saving, so the provenance the server now
    // reports is what shows.
    expect(
      within(rowOf(screen.getByLabelText("workers"))).queryByText("overridden"),
    ).toBeNull();
  });

  it("never makes a secret editable", async () => {
    stubApi();
    await renderSettings();

    const panel = screen.getByRole("heading", { name: "Secrets" }).closest("section");
    expect(panel).not.toBeNull();
    expect(within(panel as HTMLElement).queryAllByRole("textbox")).toHaveLength(0);
    expect(within(panel as HTMLElement).getByText("redacted")).toBeInTheDocument();
    expect(screen.queryByLabelText("secrets.plex_token")).toBeNull();
  });

  it("does not render the provenance keys as configuration", async () => {
    stubApi();
    await renderSettings();

    // `overridden_paths` and `frozen_paths` are not settings; rendered as
    // sections they would offer the operator an edit the API cannot accept.
    expect(screen.queryByRole("heading", { name: "Frozen paths" })).toBeNull();
    expect(screen.queryByText("Overridden paths")).toBeNull();
    expect(document.body.textContent).not.toContain(
      "the worker pool is sized once, at startup",
    );
    // `version` is a plain config field and keeps rendering.
    expect(screen.getByLabelText("version")).toHaveValue("abc123");
  });

  it("picks the widget from the value's type", async () => {
    stubApi();
    await renderSettings();

    expect(screen.getByLabelText("badges.enabled")).toHaveAttribute("type", "checkbox");
    expect(screen.getByLabelText("workers")).toHaveAttribute("type", "number");
    expect(screen.getByLabelText("plex.url")).toHaveAttribute("type", "text");
    expect(screen.getByLabelText("plex.excluded_libraries[0]")).toHaveAttribute(
      "type",
      "text",
    );
  });

  it("edits a string list as a whole list at its own path", async () => {
    const fetchMock = stubApi();
    await renderSettings();

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
      document: { plex: { excluded_libraries: ["Photos", "Anime"] } },
    });
  });

  it("toggles a boolean into the document", async () => {
    const fetchMock = stubApi();
    await renderSettings();

    fireEvent.click(screen.getByLabelText("badges.enabled"));
    await save();

    expect(putDocument(fetchMock)).toEqual({ document: { badges: { enabled: false } } });
  });

  it("clearing a number input takes the field back out of the document", async () => {
    const fetchMock = stubApi();
    await renderSettings();

    const field = screen.getByLabelText("workers");
    fireEvent.change(field, { target: { value: "9" } });
    expect(screen.getByRole("heading", { name: "Pending changes" })).toBeInTheDocument();

    // An empty number input is not "workers = 0" and it is not "workers =
    // null" either -- the operator emptied a box mid-edit. The field goes back
    // to the served value and the document stops carrying it, so there is
    // nothing pending to save.
    fireEvent.change(field, { target: { value: "" } });
    expect(screen.queryByRole("heading", { name: "Pending changes" })).toBeNull();
    expect(screen.getByLabelText("workers")).toHaveValue(5);

    fireEvent.change(field, { target: { value: "9" } });
    await save();
    expect(putDocument(fetchMock)).toEqual({ document: { workers: 9 } });
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

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await save();

    const panel = screen
      .getByRole("heading", { name: "Pending changes" })
      .closest("section") as HTMLElement;
    expect(within(panel).getByText("the database is unreachable")).toBeInTheDocument();
    // A failed save is not a save, and it must not throw the edit away either:
    // the document is still pending and still saveable.
    expect(screen.queryByText(/Saved/)).toBeNull();
    expect(within(panel).getByText(/"workers": 9/)).toBeInTheDocument();
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
  overridden_paths: ["notifications.enabled", "notifications.url"],
  redacted_paths: ["notifications.url"],
  keep_sentinel: KEEP,
};

describe("Settings editor, redacted values", () => {
  it("seeds a redacted override with the server's keep marker, not with what it was served", async () => {
    const fetchMock = stubApi({ config: REDACTED_CONFIG });
    await renderSettings();

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await save();

    const body = putDocument(fetchMock);
    expect(body).toEqual({
      document: {
        workers: 9,
        notifications: { enabled: true, url: KEEP },
      },
    });
    // The exact failure: the served host submitted back as the override.
    expect(JSON.stringify(body)).not.toContain("kuma.example.com");
  });

  it("shows the served redaction in the field and says what typing into it does", async () => {
    stubApi({ config: REDACTED_CONFIG });
    await renderSettings();

    const field = screen.getByLabelText("notifications.url");
    // The marker is a state, not a value -- an operator must never be shown
    // "***KEEP***" as the current setting.
    expect(field).toHaveValue("kuma.example.com");
    expect(field).toHaveAttribute("title", REDACTED_EDIT_NOTE);
  });

  it("shows the keep marker in the pending document, never the truncated value", async () => {
    stubApi({ config: REDACTED_CONFIG });
    await renderSettings();

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });

    const pre = document.querySelector(".config-document") as HTMLElement;
    expect(pre.textContent).toContain(KEEP);
    expect(pre.textContent).not.toContain("kuma.example.com");
  });

  it("replaces the keep marker with what is typed into the field", async () => {
    const fetchMock = stubApi({ config: REDACTED_CONFIG });
    await renderSettings();

    const url = "https://kuma.example.com/api/push/newToken";
    fireEvent.change(screen.getByLabelText("notifications.url"), {
      target: { value: url },
    });
    expect(screen.getByLabelText("notifications.url")).toHaveValue(url);
    await save();

    const body = putDocument(fetchMock);
    expect(body).toEqual({
      document: { notifications: { enabled: true, url } },
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

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await save();

    expect(putDocument(fetchMock)).toEqual({
      document: {
        workers: 9,
        notifications: { enabled: true, url: "kuma.example.com" },
      },
    });
  });

  it("does not render the redaction contract as configuration", async () => {
    stubApi({ config: REDACTED_CONFIG });
    await renderSettings();

    expect(screen.queryByLabelText("keep_sentinel")).toBeNull();
    expect(screen.queryByText("Redacted paths")).toBeNull();
    expect(screen.queryByText("Keep sentinel")).toBeNull();
  });
});
