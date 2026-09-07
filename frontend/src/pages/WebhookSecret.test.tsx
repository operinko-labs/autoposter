import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WebhookSecret } from "./WebhookSecret";

const VALUE = "row-255-revealed-secret";

async function click(name: string | RegExp) {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name }));
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("WebhookSecret", () => {
  it("shows the value with the line saying it will not return", () => {
    render(<WebhookSecret value={VALUE} />);

    expect(screen.getByTestId("webhook-secret-value")).toHaveTextContent(VALUE);
    expect(screen.getByText(/will not be shown again/i)).toBeInTheDocument();
  });

  it("reports a successful copy", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });

    render(<WebhookSecret value={VALUE} />);
    await click("Copy");

    expect(writeText).toHaveBeenCalledWith(VALUE);
    expect(screen.getByTestId("webhook-copy-status")).toHaveTextContent("Copied");
  });

  it("falls back to a selection when there is no clipboard API", async () => {
    // `navigator.clipboard` is undefined outside a secure context, which is
    // exactly the shape a plain-HTTP compose deployment runs in. A click that
    // silently did nothing would read as success to an operator who then moves
    // on without the one value this page ever shows them.
    vi.stubGlobal("navigator", { ...navigator, clipboard: undefined });

    render(<WebhookSecret value={VALUE} />);
    await click("Copy");

    expect(screen.getByTestId("webhook-copy-status")).toHaveTextContent(
      "Select and copy the value above.",
    );
  });
});
