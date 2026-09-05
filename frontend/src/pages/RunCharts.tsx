import { useEffect, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { apiFetch } from "../api/client";
import type { RunEntry, RunsResponse } from "../api/types";

/** How many runs to chart. The server's own default; named here so the URL
 * this component requests is readable in one place. */
const LIMIT = 50;

/** Explicit pixel dimensions rather than Recharts' ResponsiveContainer.
 *
 * ResponsiveContainer measures its parent through a ResizeObserver, which
 * jsdom does not implement -- so the component's own test would either have to
 * mock the container away (proving nothing about the chart) or install a
 * ResizeObserver polyfill for one assertion. A fixed canvas inside the
 * `.table-scroll` wrapper the dashboard's tables already use gives the same
 * behaviour on a narrow screen (it scrolls) with none of that. */
const CHART_WIDTH = 720;
const CHART_HEIGHT = 220;

/** One bar's label: the job name plus the clock time it started, which is what
 * makes fifty bars of `stale_job_reclaim` distinguishable from each other. */
function label(run: RunEntry): string {
  const at = new Date(run.started_at);
  const time = Number.isNaN(at.getTime())
    ? ""
    : at.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return `${run.name} ${time}`.trim();
}

export function RunCharts() {
  const [runs, setRuns] = useState<RunEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The dashboard's own live/unmounted guard: this fetch resolves into a
  // setState that must not run after the page has gone.
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  // Once, on mount. Deliberately NOT on the dashboard's NDJSON stream: that
  // stream republishes a whole snapshot whenever the status changes (every two
  // seconds at worst), and a chart of history has no business being recomputed
  // at that cadence. Run history moves when a pass ends, which is minutes to
  // hours apart.
  useEffect(() => {
    async function load() {
      try {
        const body = await apiFetch<RunsResponse>(`/api/stats/runs?limit=${LIMIT}`);
        if (live.current) setRuns(body.runs);
      } catch (caught) {
        if (live.current) setError((caught as Error).message);
      }
    }
    void load();
  }, []);

  if (error !== null) return <p className="page-error">{error}</p>;
  if (runs === null) return <p className="muted">Loading run history…</p>;
  if (runs.length === 0) return <p className="empty">No runs recorded yet.</p>;

  // The endpoint answers newest first, which is right for a list and wrong for
  // a time axis: reversed here rather than server-side so the JSON stays in the
  // order a widget reading `runs.0` expects.
  const chronological = [...runs].reverse();
  const durations = chronological
    .filter((run) => run.duration_seconds !== null)
    .map((run) => ({ label: label(run), seconds: run.duration_seconds as number }));
  // Only attributed runs. A null is "not measured", and plotting it as a zero
  // would claim a full pass's neighbours did nothing.
  const counts = chronological
    .filter((run) => run.processed !== null)
    .map((run) => ({
      label: label(run),
      processed: run.processed as number,
      failed: run.failed as number,
    }));

  return (
    <div className="run-charts">
      <section className="panel">
        <h2>Run duration</h2>
        <div className="table-scroll">
          <BarChart width={CHART_WIDTH} height={CHART_HEIGHT} data={durations}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="label" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
            <YAxis tick={{ fontSize: 10 }} />
            <Tooltip />
            {/* Animation off: the bars' final geometry is what a test reads,
                and a chart that redraws on every re-render of a live dashboard
                is a distraction rather than a feature. */}
            <Bar dataKey="seconds" name="Seconds" fill="#4c8dff" isAnimationActive={false} />
          </BarChart>
        </div>
      </section>

      <section className="panel">
        <h2>Processed and failed per run</h2>
        <p className="muted">
          Counts are recorded for full passes only — a scheduled job's window
          overlaps whatever the worker pool was doing, so that work is not
          claimed as its own.
        </p>
        {counts.length === 0 ? (
          <p className="empty">No full pass has completed yet.</p>
        ) : (
          <div className="table-scroll">
            <BarChart width={CHART_WIDTH} height={CHART_HEIGHT} data={counts}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="label" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
              <YAxis tick={{ fontSize: 10 }} />
              <Tooltip />
              <Legend />
              <Bar
                dataKey="processed"
                stackId="outcome"
                name="Processed"
                fill="#3fb27f"
                isAnimationActive={false}
              />
              <Bar
                dataKey="failed"
                stackId="outcome"
                name="Failed"
                fill="#e2564a"
                isAnimationActive={false}
              />
            </BarChart>
          </div>
        )}
      </section>
    </div>
  );
}
