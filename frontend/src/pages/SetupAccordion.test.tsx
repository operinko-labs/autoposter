import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SetupAccordion } from "./SetupAccordion";

function respond(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

const SAVED = async () => true;

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => respond({ ok: true, detail: "Sonarr answered." })));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SetupAccordion", () => {
  it("opens a required system and collapses an optional one", () => {
    render(
      <>
        <SetupAccordion system="tmdb" label="TMDb" credential="AUTOPOSTER_TMDB_TOKEN"
          required held={null} needsAddress={false} onSave={SAVED} />
        <SetupAccordion system="mdblist" label="MDBList" credential="AUTOPOSTER_MDBLIST_APIKEY"
          required={false} held={null} needsAddress={false} onSave={SAVED} />
      </>,
    );

    expect(screen.getByTestId("accordion-body-tmdb")).toBeInTheDocument();
    expect(screen.queryByTestId("accordion-body-mdblist")).toBeNull();
  });

  it("collapses a system whose credential is already stored, with a Stored pill on the header", () => {
    render(
      <SetupAccordion system="tmdb" label="TMDb" credential="AUTOPOSTER_TMDB_TOKEN"
        required held="***REDACTED***" needsAddress={false} onSave={SAVED} />,
    );

    expect(screen.queryByTestId("accordion-body-tmdb")).toBeNull();
    expect(screen.getByTestId("held-AUTOPOSTER_TMDB_TOKEN")).toHaveTextContent("Stored");
    expect(document.body.textContent).not.toContain("***REDACTED***");
  });

  it("shows the address field only for a system whose address the operator supplies", () => {
    const { rerender } = render(
      <SetupAccordion system="sonarr" label="Sonarr" credential="AUTOPOSTER_SONARR_APIKEY"
        required={false} held={null} needsAddress onSave={SAVED} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Sonarr/ }));
    expect(screen.getByLabelText("Sonarr address")).toBeInTheDocument();

    rerender(
      <SetupAccordion system="tmdb" label="TMDb" credential="AUTOPOSTER_TMDB_TOKEN"
        required held={null} needsAddress={false} onSave={SAVED} />,
    );
    expect(screen.queryByLabelText("TMDb address")).toBeNull();
  });

  it("renders the server's answered sentence on the header after a check", async () => {
    render(
      <SetupAccordion system="sonarr" label="Sonarr" credential="AUTOPOSTER_SONARR_APIKEY"
        required={false} held={null} needsAddress onSave={SAVED} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Sonarr/ }));
    fireEvent.change(screen.getByLabelText("Sonarr address"), {
      target: { value: "http://sonarr.invalid:8989" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Check connection" }));

    await waitFor(() =>
      expect(screen.getByTestId("check-result-sonarr")).toHaveTextContent("Sonarr answered."),
    );
  });

  it("renders the server's refusal sentence verbatim, because it is fixed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => respond({ ok: false, detail: "Sonarr refused the credential." })),
    );
    render(
      <SetupAccordion system="sonarr" label="Sonarr" credential="AUTOPOSTER_SONARR_APIKEY"
        required={false} held={null} needsAddress onSave={SAVED} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Sonarr/ }));
    fireEvent.change(screen.getByLabelText("Sonarr address"), {
      target: { value: "http://sonarr.invalid:8989" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Check connection" }));

    await waitFor(() =>
      expect(screen.getByTestId("check-result-sonarr")).toHaveTextContent(
        "Sonarr refused the credential.",
      ),
    );
  });

  it("never sends open or closed to the server", () => {
    const fetchMock = vi.fn(async () => respond({ ok: true, detail: "TMDb answered." }));
    vi.stubGlobal("fetch", fetchMock);
    render(
      <SetupAccordion system="tmdb" label="TMDb" credential="AUTOPOSTER_TMDB_TOKEN"
        required held={null} needsAddress={false} onSave={SAVED} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /TMDb/ }));
    fireEvent.click(screen.getByRole("button", { name: /TMDb/ }));

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("starts the credential input empty whatever is already stored", () => {
    render(
      <SetupAccordion system="tmdb" label="TMDb" credential="AUTOPOSTER_TMDB_TOKEN"
        required held="***REDACTED***" needsAddress={false} onSave={SAVED} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /TMDb/ }));

    expect(screen.getByLabelText<HTMLInputElement>("AUTOPOSTER_TMDB_TOKEN").value).toBe("");
  });
});
