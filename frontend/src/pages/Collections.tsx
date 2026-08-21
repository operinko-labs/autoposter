import { useEffect, useState } from "react";

import { apiFetch } from "../api/client";
import type { CollectionSummary, CollectionsResponse } from "../api/types";

export function Collections() {
  const [collections, setCollections] = useState<CollectionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiFetch<CollectionsResponse>("/api/collections")
      .then((response) => {
        if (!cancelled) setCollections(response.collections);
      })
      .catch((caught: Error) => {
        if (!cancelled) setError(caught.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <div className="page-header">
        <h1>Collections</h1>
        {collections !== null && <span className="muted">{collections.length} managed</span>}
      </div>

      {error !== null && <p className="page-error">{error}</p>}

      {/* Read-only for now. Member counts, the last diff result and a "diff
          now" action all need endpoints that do not exist yet -- see phase 4c
          in the plan. Showing an empty column would imply the data is
          there and zero. */}
      <div className="panel">
        {collections === null ? (
          <p className="muted">Loading…</p>
        ) : collections.length === 0 ? (
          <p className="empty">No managed collections yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Title</th>
                <th>Library</th>
                <th>Kind</th>
              </tr>
            </thead>
            <tbody>
              {collections.map((collection) => (
                <tr key={collection.id}>
                  <td>{collection.title}</td>
                  <td className="muted">{collection.library}</td>
                  <td>{collection.kind}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
