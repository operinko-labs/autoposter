/** The season/episode suffix Plex users read titles by, or null when the job
 * is not for an episode or a season.
 *
 * Structural rather than typed to one response: the queue, the parked list and
 * the warnings panel all lift the same two payload fields off three different
 * job shapes, and each page had grown its own copy of these four lines. */
export function seasonEpisode(job: {
  season_number: number | null;
  episode_number: number | null;
}): string | null {
  const { season_number: season, episode_number: episode } = job;
  if (season === null) return null;
  const padded = String(season).padStart(2, "0");
  if (episode === null) return `S${padded}`;
  return `S${padded}E${String(episode).padStart(2, "0")}`;
}

/** Timestamps arrive as ISO strings from the API, or null where a thing has
 * never happened. Rendering "Invalid Date" for the null case is the bug this
 * exists to avoid. */
export function formatTime(value: string | null): string {
  if (value === null || value === "") return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** How long ago `value` was, as a short phrase -- "since 5m", "since 2h" --
 * for a run the dashboard knows is still going (see Dashboard.tsx's
 * `status` pill). Floored, the opposite of Jobs.tsx's countdown-to-next-run
 * (which rounds up so "in 3m" means "within three minutes"): an elapsed
 * duration hasn't reached the next whole unit yet, so "5m" here means "at
 * least five minutes", not "any moment now". `now` defaults to the real
 * clock and is only ever overridden by a test. */
export function formatSince(value: string, now: Date = new Date()): string {
  const started = new Date(value);
  if (Number.isNaN(started.getTime())) return "—";
  const seconds = Math.max(0, Math.floor((now.getTime() - started.getTime()) / 1000));
  if (seconds < 60) return `since ${seconds}s`;
  if (seconds < 3600) return `since ${Math.floor(seconds / 60)}m`;
  return `since ${Math.floor(seconds / 3600)}h`;
}
