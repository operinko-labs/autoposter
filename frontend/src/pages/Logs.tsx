import { useEffect, useRef, useState } from "react";

import { apiFetchNdjson } from "../api/client";
import { isLogLine, type LogLine } from "../api/types";
import "./logs.css";

/** More than the server buffers (1000), so nothing the stream delivered is
 * thrown away while the tab stays open; bounded so an afternoon of tailing
 * cannot grow the page without limit. */
const MAX_LINES = 2000;

const LEVELS = ["ALL", "INFO", "WARNING", "ERROR"] as const;

/** How long to wait before reconnecting a dropped stream. */
const RECONNECT_MS = 3000;

function matchesLevel(line: LogLine, level: string): boolean {
  if (level === "ALL") return true;
  if (level === "ERROR") return line.level === "ERROR" || line.level === "CRITICAL";
  if (level === "WARNING") return line.level !== "INFO" && line.level !== "DEBUG";
  return true; // INFO and above is everything the server logs.
}

export function Logs() {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [level, setLevel] = useState<(typeof LEVELS)[number]>("ALL");
  const [search, setSearch] = useState("");
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** Follow (auto-scroll) is a ref rather than state: it flips on every
   * scroll event, and re-rendering the whole line list for that would make
   * scrolling through a busy log stutter. */
  const follow = useRef(true);
  const viewport = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function connect() {
      // The server replays its whole buffer on connect, so a reconnect
      // starts from a clean slate rather than appending a duplicate backlog.
      setLines([]);
      try {
        setConnected(true);
        setError(null);
        await apiFetchNdjson(
          "/api/logs/stream",
          (value) => {
            if (!isLogLine(value)) return; // heartbeat
            setLines((prev) => {
              const next = prev.length >= MAX_LINES ? prev.slice(-MAX_LINES + 1) : prev.slice();
              next.push(value);
              return next;
            });
          },
          controller.signal,
        );
      } catch (caught) {
        if (controller.signal.aborted) return;
        setError((caught as Error).message);
      }
      if (controller.signal.aborted) return;
      // The server ended the stream (a restart, a proxy timeout) or the
      // transport failed; either way the tail should resume by itself.
      setConnected(false);
      timer = setTimeout(() => void connect(), RECONNECT_MS);
    }

    void connect();
    return () => {
      controller.abort();
      if (timer !== undefined) clearTimeout(timer);
    };
  }, []);

  // Stick to the bottom while the operator has not scrolled away from it.
  useEffect(() => {
    const el = viewport.current;
    if (el !== null && follow.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  function onScroll() {
    const el = viewport.current;
    if (el === null) return;
    follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  }

  const needle = search.trim().toLowerCase();
  const visible = lines.filter(
    (line) =>
      matchesLevel(line, level) &&
      (needle === "" ||
        line.message.toLowerCase().includes(needle) ||
        line.logger.toLowerCase().includes(needle)),
  );

  return (
    <>
      <div className="page-header">
        <h1>Logs</h1>
        <span className={connected ? "log-status live" : "log-status"}>
          {connected ? "live" : "reconnecting…"}
        </span>
      </div>

      {error !== null && <p className="page-error">{error}</p>}

      <div className="log-controls">
        <label>
          Level{" "}
          <select value={level} onChange={(e) => setLevel(e.target.value as typeof level)}>
            {LEVELS.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <input
          type="search"
          placeholder="Filter…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <span className="muted">
          {visible.length} of {lines.length} line{lines.length === 1 ? "" : "s"}
        </span>
      </div>

      <div className="panel log-viewport" ref={viewport} onScroll={onScroll}>
        {visible.length === 0 ? (
          <p className="empty">Nothing yet.</p>
        ) : (
          visible.map((line, index) => (
            <div key={index} className={`log-line level-${line.level.toLowerCase()}`}>
              <span className="log-ts">{line.ts.slice(11, 19)}</span>
              <span className="log-level">{line.level}</span>
              <span className="log-logger">{line.logger}</span>
              <span className="log-message">{line.message}</span>
            </div>
          ))
        )}
      </div>
    </>
  );
}
