import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SetupJellyfinPane } from "./SetupJellyfinPane";

const ADDRESS = "https://jellyfin.invalid:8096";
const KEY = "row-267-typed-api-key";
// `{id, name, type}` -- `setup_jellyfin.library_list`'s own shape, which is
// NOT the Plex route's `{key, title, type}`. The exclusion list the config
// document takes is spelled by NAME.
const LIBRARIES = [
  { id: "lib1", name: "Movies", type: "movies" },
  { id: "lib2", name: "Photos", type: "photos" },
];

function respond(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

function transport() {
  return vi.fn(async (path: unknown) =>
    path === "/api/setup/jellyfin/libraries"
      ? respond({ libraries: LIBRARIES })
      : respond({ ok: true }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SetupJellyfinPane", () => {
  it("reads the libraries at the accordion's address, with the key typed beside it", async () => {
    // Jellyfin has ONE arrival where Plex has two: there is no account
    // sign-in to pick a server from, so the address and the key are always the
    // operator's own -- the two fields the accordion above this pane holds.
    const fetchMock = transport();
    vi.stubGlobal("fetch", fetchMock);
    render(<SetupJellyfinPane address={ADDRESS} credentialValue={KEY} onSelect={async () => true} />);

    fireEvent.click(screen.getByRole("button", { name: "Read libraries" }));

    await waitFor(() => expect(screen.getByLabelText("Movies")).toBeChecked());
    expect(screen.getByLabelText("Photos")).toBeChecked();
    const read = fetchMock.mock.calls.find(([path]) => path === "/api/setup/jellyfin/libraries");
    expect(JSON.parse(String(read?.[1]?.body))).toEqual({
      base_url: ADDRESS,
      credential_value: KEY,
    });
  });

  it("sends no credential when the field is empty, because empty means keep", async () => {
    // The rule every credential field in this wizard has: an untouched field
    // reads with the key the wizard already staged, and the server answers
    // CHECK_NEEDS_A_TYPED_CREDENTIAL when it holds none either.
    const fetchMock = transport();
    vi.stubGlobal("fetch", fetchMock);
    render(<SetupJellyfinPane address={ADDRESS} credentialValue="" onSelect={async () => true} />);

    fireEvent.click(screen.getByRole("button", { name: "Read libraries" }));

    await waitFor(() => expect(screen.getByLabelText("Movies")).toBeChecked());
    const read = fetchMock.mock.calls.find(([path]) => path === "/api/setup/jellyfin/libraries");
    expect(JSON.parse(String(read?.[1]?.body))).toEqual({
      base_url: ADDRESS,
      credential_value: null,
    });
  });

  it("submits this server's address WITH its unticked libraries, and nothing of the other", async () => {
    // Two things at once. The exclusion list is the tick-list's COMPLEMENT,
    // because the schema's field is `jellyfin.excluded_libraries`; and the
    // address goes with it in the same body, because `/api/setup/config`
    // reads a body with no url at all as "keep" and ignores a tick-list sent
    // alone. The two `null`s are the Plex card's pair: this card submits its
    // own two fields and never the other card's.
    const onSelect = vi.fn(async () => true);
    vi.stubGlobal("fetch", transport());
    render(<SetupJellyfinPane address={ADDRESS} credentialValue={KEY} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: "Read libraries" }));
    await waitFor(() => screen.getByLabelText("Photos"));

    fireEvent.click(screen.getByLabelText("Photos"));
    fireEvent.click(screen.getByRole("button", { name: "Use this server" }));

    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(null, null, ADDRESS, ["Photos"]));
  });

  it("still submits, with no exclusions, when the library list could not be read", async () => {
    // The Plex pane's rule for the same shape: an address the pod can reach
    // for the config document but not for a library read is still an address
    // worth recording, and the alternative is a wizard that cannot be
    // finished. The empty list is the honest answer -- every library managed
    // -- and it is a different answer from absent, which would leave the
    // example document's own exclusions on this deployment.
    const onSelect = vi.fn(async () => true);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: unknown) =>
        path === "/api/setup/jellyfin/libraries"
          ? respond({ detail: "Jellyfin could not be reached (ConnectError)." }, 502)
          : respond({ ok: true }),
      ),
    );
    render(<SetupJellyfinPane address={ADDRESS} credentialValue={KEY} onSelect={onSelect} />);

    fireEvent.click(screen.getByRole("button", { name: "Read libraries" }));

    await waitFor(() =>
      expect(screen.getByTestId("jellyfin-libraries-unread")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Use this server" }));

    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(null, null, ADDRESS, []));
  });

  it("shows the server's fixed sentence when the read is refused", async () => {
    const detail =
      "a credential must be typed here, or saved first: this check does not read the one the " +
      "deployment already holds";
    vi.stubGlobal("fetch", vi.fn(async () => respond({ detail }, 400)));
    render(<SetupJellyfinPane address={ADDRESS} credentialValue="" onSelect={async () => true} />);

    fireEvent.click(screen.getByRole("button", { name: "Read libraries" }));

    await waitFor(() => expect(screen.getByText(detail)).toBeInTheDocument());
  });

  it("offers nothing to read or submit before an address is typed", async () => {
    // The accordion's address field is this pane's only input, and a read of
    // an empty address is a refusal the operator did not need to collect.
    vi.stubGlobal("fetch", transport());
    render(<SetupJellyfinPane address="  " credentialValue={KEY} onSelect={async () => true} />);

    expect(screen.queryByRole("button", { name: "Read libraries" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Use this server" })).toBeNull();
  });

  it("offers no submit on a deployment that provides its own document, and says why", () => {
    // `POST /api/setup/config` is refused while a document already resolves
    // (facts Amendment 6), so both controls that post to it would only ever
    // collect a fixed refusal. The API KEY field is the accordion's own and
    // stays: this deployment's document may name Jellyfin with no key stored,
    // which is exactly the shape the media-server step is unmet for.
    vi.stubGlobal("fetch", transport());
    render(
      <SetupJellyfinPane
        address={ADDRESS}
        configSource="configured"
        credentialValue={KEY}
        onSelect={async () => true}
      />,
    );

    expect(screen.getByTestId("jellyfin-configured")).toBeInTheDocument();
    expect(screen.queryByTestId("jellyfin-state-document")).toBeNull();
    expect(screen.queryByRole("button", { name: "Read libraries" })).toBeNull();
  });

  it("names a document an EARLIER FINISH wrote as that, not as the deployment's own", () => {
    // `stage_config_document` refuses on `config_document_path() is not None`,
    // which is `"configured"` AND `"state"` -- and `"state"` is reachable by
    // design: Amendment 3's write order makes a finish interrupted between the
    // config write and the secrets write the survivable window. Its own
    // sentence, because "this deployment provides its own document" is false
    // here: this wizard wrote it last run.
    vi.stubGlobal("fetch", transport());
    render(
      <SetupJellyfinPane
        address={ADDRESS}
        configSource="state"
        credentialValue={KEY}
        onSelect={async () => true}
      />,
    );

    expect(screen.getByTestId("jellyfin-state-document")).toBeInTheDocument();
    expect(screen.queryByTestId("jellyfin-configured")).toBeNull();
    expect(screen.queryByRole("button", { name: "Read libraries" })).toBeNull();
  });
});
