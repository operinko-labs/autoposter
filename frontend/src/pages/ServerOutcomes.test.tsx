import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ServerOutcomes from "./ServerOutcomes";

const ABSENT = "library: not carried by this server";

describe("ServerOutcomes", () => {
  it("shows one row per server with both statuses and the time", () => {
    render(
      <ServerOutcomes
        servers={[
          {
            server: "jellyfin",
            metadata: {
              status: "failed",
              detail: "status: HTTPStatusError 400",
              attempts: 8,
              attempted_at: "2026-09-14T00:00:00Z",
              written_at: null,
              next_attempt_at: null,
            },
            artwork: [
              {
                art_kind: "poster",
                status: "uploaded",
                detail: null,
                attempts: 0,
                attempted_at: "2026-09-14T00:00:00Z",
                uploaded_at: "2026-09-14T00:00:00Z",
                next_attempt_at: null,
              },
            ],
          },
        ]}
      />,
    );

    expect(screen.getByText("jellyfin")).toBeInTheDocument();
    expect(screen.getByText("metadata failed")).toBeInTheDocument();
    expect(screen.getByText("poster uploaded")).toBeInTheDocument();
    expect(screen.getByTitle("status: HTTPStatusError 400")).toBeInTheDocument();
  });

  it("renders an absent server as one neutral line", () => {
    render(
      <ServerOutcomes
        servers={[
          {
            server: "jellyfin",
            metadata: {
              status: "absent",
              detail: ABSENT,
              attempts: 0,
              attempted_at: "2026-09-14T00:00:00Z",
              written_at: null,
              next_attempt_at: null,
            },
            artwork: [
              {
                art_kind: "poster",
                status: "absent",
                detail: ABSENT,
                attempts: 0,
                attempted_at: "2026-09-14T00:00:00Z",
                uploaded_at: null,
                next_attempt_at: null,
              },
            ],
          },
        ]}
      />,
    );

    expect(screen.getByText("jellyfin does not carry this library")).toBeInTheDocument();
    expect(screen.queryByText("metadata absent")).not.toBeInTheDocument();
  });

  it("says so when nothing has been recorded", () => {
    render(<ServerOutcomes servers={[]} />);
    expect(screen.getByText("No per-server outcome recorded yet.")).toBeInTheDocument();
  });
});
