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
  plex: {
    url: "http://plex:32400",
    excluded_libraries: ["Muskarit", "Photos"],
    resolve_max_attempts: 10,
  },
  badges: { enabled: true },
  artwork: { poster: { text: { min_point_size: 20 } } },
  secrets: { plex_token: REDACTED },
  overridden_paths: [] as string[],
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
    fireEvent.click(screen.getByRole("button", { name: "Save only" }));
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

  it("reports an inert change separately from the restart list, honestly", async () => {
    // api_docs_enabled is frozen into the running application object before
    // any override is read -- a restart cannot apply it either, only editing
    // the mounted config file can. Folding it into "Restart required to
    // apply" would promise the operator a fix that restarting can never
    // deliver.
    stubApi({
      put: json({
        version_before: "abc123",
        version_after: "abc123",
        restart_required: [],
        inert: ["api_docs_enabled"],
      }),
    });
    await renderSettings();

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await save();

    expect(
      screen.getByText(
        "Has no effect until it changes in the deployed config file: api_docs_enabled",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Restart required to apply/)).toBeNull();
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

  it("hangs each setting's description off its label as hover text", async () => {
    stubApi();
    await renderSettings();

    const label = within(rowOf(screen.getByLabelText("workers"))).getByText(
      "Workers",
    );
    expect(label).toHaveAttribute(
      "title",
      "How many render workers run in parallel.",
    );
    // A path the server described nothing for gets no empty tooltip: an empty
    // `title` is a hover that opens onto nothing.
    expect(
      within(rowOf(screen.getByLabelText("badges.enabled"))).getByText("Enabled"),
    ).not.toHaveAttribute("title");
  });

  it("hangs a description off a secrets row too, since redacted text is all it shows", async () => {
    stubApi();
    await renderSettings();

    // The secrets panel gets no editor (editor={null}) and renders only the
    // "redacted" pill -- the description is the one informative thing left
    // on the row, which is why `descriptions` is a prop of ConfigSections
    // independent of `editor` (Settings.tsx:445-449's design comment).
    const label = within(rowOf(screen.getByText("Plex token"))).getByText(
      "Plex token",
    );
    expect(label).toHaveAttribute(
      "title",
      "The Plex authentication token this service connects with.",
    );
  });

  it("renders a computed path read-only rather than offering an inert edit", async () => {
    stubApi();
    await renderSettings();

    // `version` is derived from the other settings; an override on it is
    // recomputed away, so the editor must not present it as a field.
    expect(screen.queryByLabelText("version")).toBeNull();
    expect(screen.getByText("abc123")).toBeInTheDocument();
  });

  it("does not demand a restart for a live path inside a frozen section", async () => {
    stubApi();
    await renderSettings();

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
    expect(screen.getByRole("button", { name: "Save only" })).toBeEnabled();
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

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await click("Preview");

    const call = callTo(fetchMock, "/api/config/preview");
    expect(call.method).toBe("POST");
    expect(call.body).toEqual({
      document: { artwork: { poster: { text: { min_point_size: 24 } } } },
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

  it("does not claim the gated breakdown's kinds are the shape of the library", async () => {
    // `affected` below `of_total` only happens through a gate (a disabled
    // kind, or `skip_tba`), and a gate is a real signal about what the edit
    // touched -- unlike the whole-library case, where every kind is
    // affected regardless of what the edit was.
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

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await click("Preview");

    const panel = pendingPanel();
    // The gate half holds regardless of whether the edit was gated or not.
    expect(
      within(panel).getByText(
        /a kind missing from the breakdown was excluded by a gate/i,
      ),
    ).toBeInTheDocument();
    // The "singled out" sentence is only true in the whole-library case; a
    // gated preview's kinds are a real signal, so it must not be there.
    expect(panel.textContent).not.toMatch(/singled out/i);
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

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await click("Preview");

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

  it("says plainly that an artwork edit re-renders everything, rather than implying it picked rows", async () => {
    // `config.version` hashes the whole artwork section, so any artwork edit
    // invalidates every fingerprinted row and `affected` equals the whole
    // examined population. A breakdown presented without this reads as though
    // the edit selected those kinds; it did not.
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

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await click("Preview");

    const panel = pendingPanel();
    expect(
      within(panel).getByText(
        /any artwork change re-renders the whole library — ~9 of 9 artwork renders/i,
      ),
    ).toBeInTheDocument();
    // In the whole-library case the breakdown really is just the shape of the
    // library, so both halves of the note apply.
    expect(
      within(panel).getByText(
        /a kind counted below is not one the edit singled out/i,
      ),
    ).toBeInTheDocument();
    expect(
      within(panel).getByText(
        /a kind missing from the breakdown was excluded by a gate/i,
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

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await click("Preview");

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

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await save();
    expect(screen.getByText(/abc123 → def456/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "12" } });
    await click("Preview");

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

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await click("Preview");

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

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await click("Preview");

    const panel = pendingPanel();
    expect(
      within(panel).getByText(
        "Has no effect until it changes in the deployed config file: api_docs_enabled",
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

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await click("Preview");

    const body = callTo(fetchMock, "/api/config/preview").body;
    expect(body).toEqual({
      document: { workers: 9, notifications: { enabled: true, url: KEEP } },
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

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await click("Preview");

    const panel = pendingPanel();
    expect(within(panel).getByText("the database is unreachable")).toBeInTheDocument();
    expect(within(panel).getByText(/"workers": 9/)).toBeInTheDocument();
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

    fireEvent.change(screen.getByLabelText("plex.url"), {
      target: { value: "not a url" },
    });
    await click("Preview");

    expect(
      within(rowOf(screen.getByLabelText("plex.url"))).getByText(
        "Input should be a valid URL",
      ),
    ).toBeInTheDocument();
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

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await click("Apply now");

    const call = callTo(fetchMock, "/api/config/apply");
    expect(call.method).toBe("POST");
    expect(call.body).toEqual({
      document: { artwork: { poster: { text: { min_point_size: 24 } } } },
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

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await click("Apply now");

    const body = callTo(fetchMock, "/api/config/apply").body;
    expect(body).toEqual({
      document: { workers: 9, notifications: { enabled: true, url: KEEP } },
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

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await save();

    const call = callTo(fetchMock, "/api/config/overrides");
    expect(call.method).toBe("PUT");
    expect(call.body).toEqual({
      document: { artwork: { poster: { text: { min_point_size: 24 } } } },
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

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await click("Preview");
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

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });

    // Save-only leaves stale fingerprints on disk on purpose; an operator who
    // is not told that reads "Saved" as "done".
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

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));

    for (const name of ["Preview", "Save only", "Apply now"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }

    await act(async () => {
      release(previewBody(null, "abc123"));
    });
    for (const name of ["Preview", "Save only", "Apply now"]) {
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

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await click("Preview");
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

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await click("Preview");
    expect(callTo(fetchMock, "/api/config/preview").body).toEqual({
      document: { workers: 9 },
      expected_revision: "rev-1",
    });

    await save();
    expect(callTo(fetchMock, "/api/config/overrides").body).toEqual({
      document: { workers: 9 },
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
      overridden_paths: ["badges.enabled"],
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
