/** The provider attribution block, and the wording it is required to carry.
 *
 * Extracted from Settings rather than copied: TMDB's and TheTVDB's terms make
 * this a condition of showing their data on a user-facing surface, and the
 * candidate picker became a second such surface -- the first one that shows
 * their *artwork* as theirs. Two copies of a licence condition is two things to
 * forget to update, and `tests/test_attribution_present.py` pins the built
 * bundle against the exact strings below.
 *
 * It renders no wrapper of its own. The two callers place it differently (a
 * panel of its own on Settings, inside the picker panel on an item), and the
 * one thing that must not vary -- the brand mark the TMDB logo is weighed
 * against, directly above it -- is part of the block itself.
 */

/** Imported rather than referenced as `/tmdb-logo.png` from `public/`. Vite
 * copies `public/` to the *root* of `dist/`, which `src/autoposter/api/spa.py`
 * does not serve -- it mounts `/assets` and answers everything else with
 * index.html, so the logo would come back as a 200 of HTML and render broken
 * with nothing failing loudly. Importing it puts the file under `/assets/`
 * with a content hash, where the mount already serves it. */
import tmdbLogo from "./assets/tmdb-logo.png";
import "./ProviderAttribution.css";

/** Verbatim, because TMDB's terms specify the wording. Do not paraphrase. */
export const TMDB_NOTICE =
  "This product uses TMDB and the TMDB APIs but is not endorsed, certified, " +
  "or otherwise approved by TMDB.";

/** TheTVDB's terms require attribution carrying a direct link to their site. */
export const TVDB_NOTICE =
  "Metadata provided by TheTVDB. Please consider adding missing information " +
  "or subscribing.";

export function ProviderAttribution() {
  return (
    <>
      {/* TMDB's terms require their logo to be less prominent than this
          application's own branding. The sidebar wordmark is not a sound basis
          for that comparison from a page that does not own it (see
          ProviderAttribution.css), so the block carries its own, directly above
          the logo it is being weighed against. */}
      <p className="brand-mark">Autoposter</p>

      <h2>Metadata providers</h2>

      {/* Required attribution, not decoration. Both providers' terms make this
          a condition of using their API from a user-facing surface. */}
      <div className="provider">
        <img className="provider-logo" src={tmdbLogo} alt="TMDB" width={72} height={38} />
        <p className="provider-notice">{TMDB_NOTICE}</p>
      </div>

      <div className="provider">
        <p className="provider-notice">
          {TVDB_NOTICE}{" "}
          <a href="https://thetvdb.com" target="_blank" rel="noreferrer noopener">
            TheTVDB.com
          </a>
        </p>
      </div>

      <p className="provider-notice muted">
        Artwork is also sourced from Fanart.tv, which sets no attribution
        requirement for personal API keys.
      </p>
    </>
  );
}
