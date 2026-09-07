/** Rotating the Radarr/Sonarr webhook secret, from the page that already owns
 * this deployment's configuration.
 *
 * Its own file rather than another section of `Settings.tsx` -- that page is
 * already the largest in the app and this is a self-contained thing with its
 * own fetch, its own errors and its own state. `ConfigSafetyPanel` is the
 * precedent.
 *
 * ONCE-ONLY, and simpler than the wizard's. The POST response is the only
 * serve of the value: there is no GET to replay and no served-once flag on
 * the server. A reload before the operator copies loses it, and the remedy is
 * to rotate again -- idempotent by design and one click. So the value lives
 * in this component's state and nowhere else, never in sessionStorage.
 *
 * `rotated_at` is held for this session only. The durable record is the
 * `events_log` row the endpoint writes, which the Dashboard's events list
 * already shows; a persisted "last rotated" label here would need a filter on
 * `GET /api/events` (unfiltered newest-N today) or a new endpoint, for a
 * cosmetic line.
 */
import { useState } from "react";

import { ApiError, apiFetch } from "../api/client";
import type { WebhookRotationResponse } from "../api/types";
import { WebhookSecret } from "./WebhookSecret";

const ARR_SERVICES = [
  { service: "radarr", label: "Radarr" },
  { service: "sonarr", label: "Sonarr" },
];

export function WebhookSecretPanel() {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<WebhookRotationResponse | null>(null);
  const [revealed, setRevealed] = useState(false);

  async function rotate() {
    setBusy(true);
    setError(null);
    try {
      const body = await apiFetch<WebhookRotationResponse>(
        "/api/config/webhook-secret/rotate",
        { method: "POST" },
      );
      setResult(body);
      setRevealed(true);
      setConfirming(false);
    } catch (caught) {
      // The server's own sentence, verbatim. Every refusal this API makes is
      // fixed and echoes nothing it was given, which is what lets this render
      // `detail` rather than translate a status code into a guess.
      setError(caught instanceof ApiError ? caught.message : String(caught));
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <h2>Webhook secret</h2>
      <p className="config-note" data-testid="rotate-lead">
        Rotating mints a new secret, stores it, makes it live for this service
        immediately, and re-registers Radarr and Sonarr with it where both an
        address and an API key are configured. If a re-registration fails, that
        service&apos;s deliveries are rejected until you paste the new value in
        by hand — the cost is latency, not correctness: the artwork is picked
        up later by the drift sweep and the next full pass.
      </p>

      {error !== null && (
        <p className="page-error" data-testid="rotate-error">
          {error}
        </p>
      )}

      {revealed && result !== null && (
        <>
          <WebhookSecret value={result.webhook_secret} />
          <p className="config-note" data-testid="rotate-test-hint">
            Each service&apos;s own Test button will now succeed. That proves
            the address and the header, and nothing about whether a real event
            produces artwork.
          </p>
          <ul className="config-list">
            {ARR_SERVICES.map(({ service, label }) => (
              <li key={service} data-testid={`rotation-${service}`}>
                <strong>{label}: </strong>
                {result.registrations[service]?.detail ?? ""}
              </li>
            ))}
          </ul>
          <button type="button" onClick={() => setRevealed(false)}>
            Done
          </button>
        </>
      )}

      {!revealed && result !== null && (
        <p className="config-note" data-testid="rotate-rotated-at">
          {`Rotated at ${result.rotated_at}. The value is not shown again.`}
        </p>
      )}

      {!revealed && !confirming && (
        <button type="button" disabled={busy} onClick={() => setConfirming(true)}>
          Rotate webhook secret
        </button>
      )}

      {!revealed && confirming && (
        <div data-testid="rotate-confirm">
          <p className="config-note">
            The current secret stops working the moment this completes. Have
            somewhere ready to paste the new value — it is shown once.
          </p>
          <button type="button" disabled={busy} onClick={rotate}>
            Rotate now
          </button>
          <button type="button" disabled={busy} onClick={() => setConfirming(false)}>
            Cancel
          </button>
        </div>
      )}
    </section>
  );
}
