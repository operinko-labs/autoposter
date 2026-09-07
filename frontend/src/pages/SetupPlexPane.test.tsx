import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SetupPlexPane } from "./SetupPlexPane";

// Long, because a `strong=true` PIN's code IS long -- the implementation probe
// measured 25 characters against the four the plan documented. Nothing renders
// it as something to type.
const CODE = "row-121-strong-pin-code-abcd";
const AUTH_URL = "https://app.plex.tv/auth#?clientID=row-121-client&code=row-121-strong-pin-code-abcd";
const SERVERS = [
  {
    client_identifier: "row-121-owned",
    name: "Owned",
    product: "Plex Media Server",
    platform: "Linux",
    connections: [
      { uri: "https://plex.invalid:32400", local: true, protocol: "https", port: 32400 },
    ],
  },
];
const LIBRARIES = [
  { key: "1", title: "Movies", type: "movie" },
  { key: "2", title: "Photos", type: "photo" },
];

function respond(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

function transport(authorisedAfter: number) {
  let polls = 0;
  return vi.fn(async (path: unknown, init?: RequestInit) => {
    if (path === "/api/setup/plex/pin" && init?.method === "POST") {
      return respond({ code: CODE, auth_url: AUTH_URL, expires_in: 900 });
    }
    if (path === "/api/setup/plex/pin") {
      polls += 1;
      return respond({ authorised: polls > authorisedAfter });
    }
    if (path === "/api/setup/plex/servers") return respond({ servers: SERVERS });
    if (path === "/api/setup/plex/libraries") return respond({ libraries: LIBRARIES });
    return respond({ ok: true });
  });
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("SetupPlexPane", () => {
  it("shows the PIN code and the sign-in link after minting", async () => {
    vi.stubGlobal("fetch", transport(0));
    render(<SetupPlexPane onSelect={async () => true} />);

    fireEvent.click(screen.getByRole("button", { name: "Sign in with Plex" }));

    await waitFor(() => expect(screen.getByTestId("plex-pin-code")).toHaveTextContent(CODE));
    expect(screen.getByRole("link", { name: /Open the Plex sign-in/ })).toHaveAttribute(
      "href",
      AUTH_URL,
    );
  });

  it("polls every two seconds until the operator approves", async () => {
    const fetchMock = transport(2);
    vi.stubGlobal("fetch", fetchMock);
    render(<SetupPlexPane onSelect={async () => true} />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in with Plex" }));
    await waitFor(() => screen.getByTestId("plex-pin-code"));

    await vi.advanceTimersByTimeAsync(6000);

    await waitFor(() => expect(screen.getByText("Owned")).toBeInTheDocument());
    const polls = fetchMock.mock.calls.filter(
      ([path, init]) => path === "/api/setup/plex/pin" && init?.method !== "POST",
    );
    expect(polls.length).toBe(3);
  });

  it("stops polling at the PIN's expiry and says so", async () => {
    const fetchMock = vi.fn(async (path: unknown, init?: RequestInit) => {
      if (path === "/api/setup/plex/pin" && init?.method === "POST") {
        return respond({ code: CODE, auth_url: AUTH_URL, expires_in: 4 });
      }
      return respond({ authorised: false });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SetupPlexPane onSelect={async () => true} />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in with Plex" }));
    await waitFor(() => screen.getByTestId("plex-pin-code"));

    await vi.advanceTimersByTimeAsync(10000);

    await waitFor(() => expect(screen.getByTestId("plex-pin-expired")).toBeInTheDocument());
    const before = fetchMock.mock.calls.length;
    await vi.advanceTimersByTimeAsync(10000);
    expect(fetchMock.mock.calls.length).toBe(before);
  });

  it("lists the account's servers once approved, with their local address first", async () => {
    vi.stubGlobal("fetch", transport(0));
    render(<SetupPlexPane onSelect={async () => true} />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in with Plex" }));
    await waitFor(() => screen.getByTestId("plex-pin-code"));

    await vi.advanceTimersByTimeAsync(2000);

    await waitFor(() => expect(screen.getByText("Owned")).toBeInTheDocument());
    expect(screen.getByRole("radio", { name: /https:\/\/plex.invalid:32400/ })).toBeInTheDocument();
  });

  it("hides the PIN code and the sign-in link once the sign-in is approved", async () => {
    vi.stubGlobal("fetch", transport(0));
    render(<SetupPlexPane onSelect={async () => true} />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in with Plex" }));
    await waitFor(() => screen.getByTestId("plex-pin-code"));

    await vi.advanceTimersByTimeAsync(2000);

    await waitFor(() => expect(screen.getByText("Owned")).toBeInTheDocument());
    expect(screen.queryByTestId("plex-pin-code")).toBeNull();
    expect(screen.queryByRole("link", { name: /Open the Plex sign-in/ })).toBeNull();
  });

  it("hands the picked address to the accordion, which is what Check reads", async () => {
    // Round-2 concern 3. The accordion owns the `address` state and the Check
    // button reads THAT and nothing else, so a server picked here that does not
    // land there is checked against an empty string.
    const onAddress = vi.fn();
    vi.stubGlobal("fetch", transport(0));
    render(<SetupPlexPane onAddress={onAddress} onSelect={async () => true} />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in with Plex" }));
    await waitFor(() => screen.getByTestId("plex-pin-code"));
    await vi.advanceTimersByTimeAsync(2000);
    await waitFor(() => screen.getByText("Owned"));

    fireEvent.click(screen.getByRole("radio", { name: /https:\/\/plex.invalid:32400/ }));

    expect(onAddress).toHaveBeenCalledWith("https://plex.invalid:32400");
  });

  it("lists the chosen server's libraries as ticks, every one ticked to begin with", async () => {
    vi.stubGlobal("fetch", transport(0));
    render(<SetupPlexPane onSelect={async () => true} />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in with Plex" }));
    await waitFor(() => screen.getByTestId("plex-pin-code"));
    await vi.advanceTimersByTimeAsync(2000);
    await waitFor(() => screen.getByText("Owned"));

    fireEvent.click(screen.getByRole("radio", { name: /https:\/\/plex.invalid:32400/ }));

    await waitFor(() => expect(screen.getByLabelText("Movies")).toBeChecked());
    expect(screen.getByLabelText("Photos")).toBeChecked();
  });

  it("submits the UNTICKED libraries, because the schema's field is excluded_libraries", async () => {
    const onSelect = vi.fn(async () => true);
    vi.stubGlobal("fetch", transport(0));
    render(<SetupPlexPane onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in with Plex" }));
    await waitFor(() => screen.getByTestId("plex-pin-code"));
    await vi.advanceTimersByTimeAsync(2000);
    await waitFor(() => screen.getByText("Owned"));
    fireEvent.click(screen.getByRole("radio", { name: /https:\/\/plex.invalid:32400/ }));
    await waitFor(() => screen.getByLabelText("Photos"));

    fireEvent.click(screen.getByLabelText("Photos"));
    fireEvent.click(screen.getByRole("button", { name: "Use this server" }));

    await waitFor(() =>
      expect(onSelect).toHaveBeenCalledWith("https://plex.invalid:32400", ["Photos"]),
    );
  });

  it("never renders a token, because the server never sends one", async () => {
    vi.stubGlobal("fetch", transport(0));
    render(<SetupPlexPane onSelect={async () => true} />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in with Plex" }));
    await waitFor(() => screen.getByTestId("plex-pin-code"));
    await vi.advanceTimersByTimeAsync(2000);
    await waitFor(() => screen.getByText("Owned"));

    expect(document.body.textContent).not.toContain("Token");
    expect(document.body.textContent).not.toContain("authToken");
  });

  it("reads the libraries at the typed address, with no plex.tv sign-in at all", async () => {
    // The Critical this round closes: with `ConfigPane` gone the configuration
    // document had ONE writer and it sat behind a completed plex.tv sign-in, so
    // a deployment whose server is linked to no plex.tv account, whose pod
    // cannot reach plex.tv, or whose picked connection the pod cannot route to
    // could never finish the wizard. The accordion's own two fields are the
    // other way in, and the libraries route reads the typed token the way
    // `/check` reads its own -- for this read, staged nowhere.
    const fetchMock = transport(0);
    vi.stubGlobal("fetch", fetchMock);
    render(
      <SetupPlexPane
        address="http://plex.invalid:32400"
        credentialValue="row-121-typed-token"
        onSelect={async () => true}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Use this address" }));

    await waitFor(() => expect(screen.getByLabelText("Movies")).toBeChecked());
    const call = fetchMock.mock.calls.find(([path]) => path === "/api/setup/plex/libraries");
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      base_url: "http://plex.invalid:32400",
      credential_value: "row-121-typed-token",
    });
    // Nothing minted and nothing polled: this arrival does not touch plex.tv.
    expect(fetchMock.mock.calls.some(([path]) => path === "/api/setup/plex/pin")).toBe(false);
  });

  it("posts the SAME configuration submit from the typed address", async () => {
    // One submit path, two ways to arrive at it. A second submit would be a
    // second place for the exclusion complement to be computed wrongly.
    const onSelect = vi.fn(async () => true);
    vi.stubGlobal("fetch", transport(0));
    render(
      <SetupPlexPane
        address="http://plex.invalid:32400"
        credentialValue=""
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Use this address" }));
    await waitFor(() => screen.getByLabelText("Photos"));

    fireEvent.click(screen.getByLabelText("Photos"));
    fireEvent.click(screen.getByRole("button", { name: "Use this server" }));

    await waitFor(() =>
      expect(onSelect).toHaveBeenCalledWith("http://plex.invalid:32400", ["Photos"]),
    );
  });

  it("still submits, with no exclusions, when the library list could not be read", async () => {
    // An address the pod can reach for the config document but not for a
    // library read is still an address worth recording, and the alternative is
    // the dead end this round exists to remove. The empty list is the honest
    // answer -- every library managed -- and it is a different answer from
    // absent, which would leave the example's own two exclusions in place.
    const onSelect = vi.fn(async () => true);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: unknown) =>
        path === "/api/setup/plex/libraries"
          ? respond({ detail: "Plex could not be reached (ConnectError)." }, 502)
          : respond({ ok: true }),
      ),
    );
    render(
      <SetupPlexPane
        address="http://plex.invalid:32400"
        credentialValue=""
        onSelect={onSelect}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Use this address" }));

    await waitFor(() => expect(screen.getByTestId("plex-libraries-unread")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Use this server" }));

    await waitFor(() => expect(onSelect).toHaveBeenCalledWith("http://plex.invalid:32400", []));
  });

  it("offers no submit on a document an EARLIER FINISH wrote, and names that instead", () => {
    // Round-2 I3. `stage_config_document` refuses on
    // `config_document_path() is not None`, which is `"configured"` AND
    // `"state"` -- and `"state"` is reachable by design: Amendment 3's write
    // order makes a finish interrupted between the config write and the
    // secrets write the survivable window, and the wizard that comes back at
    // the next boot sees a document at the state path. Both controls that post
    // to that route would only ever collect a fixed refusal there.
    //
    // Its own sentence and not `CONFIGURED_DOCUMENT`'s: "this deployment
    // provides its own configuration document" is false here -- this wizard
    // wrote it last run.
    vi.stubGlobal("fetch", transport(0));
    render(
      <SetupPlexPane
        address="http://plex.invalid:32400"
        configSource="state"
        credentialValue="row-121-typed-token"
        onSelect={async () => true}
      />,
    );

    expect(screen.getByTestId("plex-state-document")).toBeInTheDocument();
    expect(screen.queryByTestId("plex-configured")).toBeNull();
    expect(screen.queryByRole("button", { name: "Use this address" })).toBeNull();
    // The sign-in stays offered: it stages the account token, which is written
    // whatever the document's source is.
    expect(screen.getByRole("button", { name: "Sign in with Plex" })).toBeInTheDocument();
  });

  it("shows the server's fixed sentence when minting fails", async () => {
    const detail = "the Plex account could not be reached (ConnectError).";
    vi.stubGlobal("fetch", vi.fn(async () => respond({ detail }, 502)));
    render(<SetupPlexPane onSelect={async () => true} />);

    fireEvent.click(screen.getByRole("button", { name: "Sign in with Plex" }));

    await waitFor(() => expect(screen.getByText(detail)).toBeInTheDocument());
  });
});
