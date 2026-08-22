/** Which art kind an item of each kind is shown as.
 *
 * This mirrors ART_KINDS_FOR in src/autoposter/render/pipeline.py -- each
 * kind's first (primary) art kind. Seasons and episodes have no `poster` row
 * at all, so asking for one would 404; movies and shows also have a
 * `background`, which the pages do not show.
 *
 * A new item kind added in Python must be added here too, or its pages fall
 * back to `poster` and 404.
 */
export const ART_KIND: Record<string, string> = {
  movie: "poster",
  show: "poster",
  season: "season_poster",
  episode: "title_card",
};

export function artKindFor(kind: string): string {
  return ART_KIND[kind] ?? "poster";
}
