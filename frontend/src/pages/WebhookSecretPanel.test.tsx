import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { WebhookSecretPanel } from "./WebhookSecretPanel";

const MINTED = "row-255-rotated-secret";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const ROTATED = {
  webhook_secret: MINTED,
  rotated_at: "2026-09-07T10:11:12Z",
  registrations: {
    radarr: {
      ok: true,
      action: "updated",
      detail: "Radarr accepted the new webhook secret (updated).",
    },
    sonarr: {
      ok: false,
      action: null,
      detail: "Sonarr would not accept the webhook registration (HTTPStatus500).",
    },
  },
};

function stub(response: Response) {
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function click(name: string | RegExp) {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name }));
  });
}

beforeEach(() => {
  setToken("row-255-session");
  vi.unstubAllGlobals();
});

describe("WebhookSecretPanel", () => {
  it("rests with the button and the honest paragraph, and no value", () => {
    stub(json(ROTATED));
    render(<WebhookSecretPanel />);

    expect(screen.getByRole("button", { name: /rotate webhook secret/i })).toBeInTheDocument();
    // The cost of a failed re-registration, said plainly: a rejected delivery
    // is a 401 before anything is written, and the artwork is picked up later
    // by the drift sweep and the full pass. Latency, not correctness.
    expect(screen.getByTestId("rotate-lead")).toHaveTextContent(/latency, not correctness/i);
    expect(screen.queryByTestId("webhook-secret-value")).not.toBeInTheDocument();
  });

  it("asks to confirm before it rotates anything", async () => {
    const fetchMock = stub(json(ROTATED));
    render(<WebhookSecretPanel />);

    await click(/rotate webhook secret/i);

    expect(screen.getByTestId("rotate-confirm")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("lets the operator back out of the confirm step", async () => {
    const fetchMock = stub(json(ROTATED));
    render(<WebhookSecretPanel />);

    await click(/rotate webhook secret/i);
    await click(/cancel/i);

    expect(screen.queryByTestId("rotate-confirm")).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows the new value once and says the arrs' own Test button now works", async () => {
    const fetchMock = stub(json(ROTATED));
    render(<WebhookSecretPanel />);

    await click(/rotate webhook secret/i);
    await click(/^rotate now$/i);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/config/webhook-secret/rotate",
      expect.objectContaining({ method: "POST" }),
    );
    expect(screen.getByTestId("webhook-secret-value")).toHaveTextContent(MINTED);
    // The confirmation the wizard could never give: on the real application
    // an *arr's Test delivery authorises, parses to zero intents and answers
    // 200. It proves the header and the URL, and nothing about artwork.
    expect(screen.getByTestId("rotate-test-hint")).toHaveTextContent(/Test/);
  });

  it("renders every per-service sentence verbatim", async () => {
    stub(json(ROTATED));
    render(<WebhookSecretPanel />);

    await click(/rotate webhook secret/i);
    await click(/^rotate now$/i);

    expect(screen.getByTestId("rotation-radarr")).toHaveTextContent(
      "Radarr accepted the new webhook secret (updated).",
    );
    expect(screen.getByTestId("rotation-sonarr")).toHaveTextContent(
      "Sonarr would not accept the webhook registration (HTTPStatus500).",
    );
    // The header VALUE is the secret and appears in exactly one place on this
    // panel; the header NAME has no business beside a result row.
    expect(document.body.textContent).not.toContain("X-Autoposter-Token");
  });

  it("renders the not-configured and public_url sentences the server sends", async () => {
    stub(
      json({
        ...ROTATED,
        registrations: {
          radarr: {
            ok: false,
            action: null,
            detail: "Radarr is not configured on this deployment.",
          },
          sonarr: {
            ok: false,
            action: null,
            detail: "public_url is empty in this deployment's configuration.",
          },
        },
      }),
    );
    render(<WebhookSecretPanel />);

    await click(/rotate webhook secret/i);
    await click(/^rotate now$/i);

    expect(screen.getByTestId("rotation-radarr")).toHaveTextContent("not configured");
    expect(screen.getByTestId("rotation-sonarr")).toHaveTextContent("public_url");
  });

  it("renders the server's refusal and shows no value", async () => {
    stub(json({ detail: "this deployment's webhook secret comes from AUTOPOSTER_WEBHOOK_SECRET" }, 400));
    render(<WebhookSecretPanel />);

    await click(/rotate webhook secret/i);
    await click(/^rotate now$/i);

    expect(screen.getByTestId("rotate-error")).toHaveTextContent(
      "AUTOPOSTER_WEBHOOK_SECRET",
    );
    expect(screen.queryByTestId("webhook-secret-value")).not.toBeInTheDocument();
  });

  it("shows when it was rotated after the value is dismissed, and never the value", async () => {
    stub(json(ROTATED));
    render(<WebhookSecretPanel />);

    await click(/rotate webhook secret/i);
    await click(/^rotate now$/i);
    await click(/done/i);

    expect(screen.queryByTestId("webhook-secret-value")).not.toBeInTheDocument();
    expect(screen.getByTestId("rotate-rotated-at")).toHaveTextContent("2026-09-07");
    expect(document.body.textContent).not.toContain(MINTED);
  });
});
