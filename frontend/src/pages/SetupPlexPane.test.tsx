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

  it("shows the server's fixed sentence when minting fails", async () => {
    const detail = "the Plex account could not be reached (ConnectError).";
    vi.stubGlobal("fetch", vi.fn(async () => respond({ detail }, 502)));
    render(<SetupPlexPane onSelect={async () => true} />);

    fireEvent.click(screen.getByRole("button", { name: "Sign in with Plex" }));

    await waitFor(() => expect(screen.getByText(detail)).toBeInTheDocument());
  });
});
