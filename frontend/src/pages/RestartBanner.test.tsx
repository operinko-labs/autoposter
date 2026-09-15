/** The banner renders the STORE's list and the server's own refusals.
 *
 * The four sentences below are `src/autoposter/api/system.py`'s, copied
 * character for character. They are asserted whole rather than matched
 * loosely because the whole contract on this path is that the page does not
 * paraphrase a refusal: each of them names something the operator has to
 * decide about, and a page-written summary of "a full pass is running" is the
 * sentence that gets a run interrupted.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RestartBanner } from "./RestartBanner";

const RESTART_IN_PROGRESS = "a restart is already under way";
const MODE_IN_FLIGHT =
  "an artwork or metadata mode is running on this instance and is writing to " +
  "a media server; restarting now would interrupt it";
const RUN_IN_FLIGHT =
  "a full_pass run is in progress (full_pass); restarting now would interrupt it";
const MULTIPLE_WORKERS =
  "this process is one of several workers sharing a port, so restarting it " +
  "would leave the others running the old configuration; restart the " +
  "deployment instead";

function stubRestart(body: unknown, status = 200) {
  const seen: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => {
      seen.push(url);
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }),
  );
  return seen;
}

async function press(name: string) {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name }));
  });
}

describe("RestartBanner", () => {
  it("renders nothing when nothing is waiting", () => {
    const { container } = render(<RestartBanner paths={[]} onRestarted={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("names the settings waiting for a restart", () => {
    render(<RestartBanner paths={["jellyfin", "workers"]} onRestarted={() => {}} />);
    expect(
      screen.getByText(
        /These settings are saved and take effect at the next restart: jellyfin, workers/,
      ),
    ).toBeInTheDocument();
  });

  it("posts the restart and tells the parent", async () => {
    const seen = stubRestart({ restarting: true });
    const restarted: boolean[] = [];
    render(
      <RestartBanner
        paths={["workers"]}
        onRestarted={() => {
          restarted.push(true);
        }}
      />,
    );

    await press("Restart now");

    expect(seen).toEqual(["/api/system/restart"]);
    expect(restarted).toEqual([true]);
  });

  it("stays disabled once the restart is accepted, and says why", async () => {
    // The process is on its way down, so there is nothing left to press until
    // this page is loaded again -- and a second press would only be answered
    // "a restart is already under way".
    stubRestart({ restarting: true });
    render(<RestartBanner paths={["workers"]} onRestarted={() => {}} />);

    await press("Restart now");

    const button = screen.getByRole("button", { name: "Restarting…" });
    expect(button).toBeDisabled();
    expect(
      screen.getByText(/This page will need loading again once the service is back/),
    ).toBeInTheDocument();
  });

  it("keeps the banner up when the re-read fails on the way down", async () => {
    // The re-read that follows a restart runs against a process that is
    // exiting. Reporting its failure would blame the restart for working.
    stubRestart({ restarting: true });
    render(
      <RestartBanner
        paths={["workers"]}
        onRestarted={() => Promise.reject(new Error("Failed to fetch"))}
      />,
    );

    await press("Restart now");

    expect(screen.queryByText("Failed to fetch")).toBeNull();
    expect(
      screen.getByText(
        /These settings are saved and take effect at the next restart: workers/,
      ),
    ).toBeInTheDocument();
  });

  it.each([
    ["a run", RUN_IN_FLIGHT],
    ["a mode", MODE_IN_FLIGHT],
    ["a restart already under way", RESTART_IN_PROGRESS],
    ["several workers", MULTIPLE_WORKERS],
  ])(
    "renders the server's refusal about %s verbatim, and does not retry",
    async (_what, sentence) => {
      const seen = stubRestart({ detail: sentence }, 409);
      render(<RestartBanner paths={["workers"]} onRestarted={() => {}} />);

      await press("Restart now");

      expect(screen.getByText(sentence)).toBeInTheDocument();
      // One post. A refused restart is offered again as a button, never as a
      // retry the page takes on its own.
      expect(seen).toEqual(["/api/system/restart"]);
      expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
    },
  );

  it("reports a network failure as a sentence of its own", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Failed to fetch")));
    render(<RestartBanner paths={["workers"]} onRestarted={() => {}} />);

    await press("Restart now");

    expect(screen.getByText("Failed to fetch")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  });
});
