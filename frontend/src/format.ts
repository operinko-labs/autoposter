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
