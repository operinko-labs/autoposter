import { memo, useEffect, useMemo, useRef, useState } from "react";
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

/** Both charts' series from the endpoint's newest-first list.
 *
 * The endpoint answers newest first, which is right for a list and wrong for
 * a time axis: reversed here rather than server-side so the JSON stays in the
 * order a widget reading `runs.0` expects. The counts chart takes attributed
 * runs only: a null is "not measured", and plotting it as a zero would claim
 * a full pass's neighbours did nothing. Those come from their own counted
 * page rather than from the recent one, because a scheduled job that records
 * every fifteen minutes fills the recent page in half a day and pushes every
 * full pass out of it. */
function deriveSeries(recent: RunEntry[], counted: RunEntry[]) {
  const durations = [...recent]
    .reverse()
    .filter((run) => run.duration_seconds !== null)
    .map((run) => ({ label: label(run), seconds: run.duration_seconds as number }));
  const counts = [...counted]
    .reverse()
    .filter((run) => run.processed !== null)
    .map((run) => ({
      label: label(run),
      processed: run.processed as number,
      failed: run.failed as number,
      status: run.status,
    }));
  return { durations, counts };
}

/** `memo`, with no props: the dashboard re-renders on every stream snapshot,
 * and nothing in a snapshot is an input to these charts (perf spec A4). The
 * component still re-renders on its own state -- the one fetch settling. */
export const RunCharts = memo(function RunCharts() {
  const [runs, setRuns] = useState<RunEntry[] | null>(null);
  const [countedRuns, setCountedRuns] = useState<RunEntry[] | null>(null);
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
        const [recent, counted] = await Promise.all([
          apiFetch<RunsResponse>(`/api/stats/runs?limit=${LIMIT}`),
          apiFetch<RunsResponse>(`/api/stats/runs?limit=${LIMIT}&counted=true`),
        ]);
        if (live.current) {
          setRuns(recent.runs);
          setCountedRuns(counted.runs);
        }
      } catch (caught) {
        if (live.current) setError((caught as Error).message);
      }
    }
    void load();
  }, []);

  // Above the early returns (a hook), and keyed on the history alone.
  const series = useMemo(
    () => (runs === null || countedRuns === null ? null : deriveSeries(runs, countedRuns)),
    [runs, countedRuns],
  );

  if (error !== null) return <p className="page-error">{error}</p>;
  if (runs === null || series === null) return <p className="muted">Loading run history…</p>;
  if (runs.length === 0) return <p className="empty">No runs recorded yet.</p>;

  const { durations, counts } = series;

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
              {/* Status appended to the label so a timed_out (or failed,
                  interrupted) run's bar is not read as a clean pass -- its
                  counts can span a sibling's whole 24h window (Minor M-1). */}
              <Tooltip
                labelFormatter={(runLabel, payload) => {
                  const status = payload[0]?.payload?.status as string | undefined;
                  return status ? `${runLabel} — ${status}` : runLabel;
                }}
              />
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
});
