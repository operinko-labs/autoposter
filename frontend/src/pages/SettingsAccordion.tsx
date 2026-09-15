import type { ReactNode } from "react";

/** One configuration section, collapsed by default.
 *
 * The header carries at most one pill (spec section 7: "accordion headers
 * carry at most two pills: the restart pill on a frozen section, and the
 * connection pill on a server card" -- and a server card is not this
 * component). The body is not rendered while closed rather than hidden with
 * CSS: a config section is dozens of inputs, and seven tabs' worth of them
 * mounted at once is what made the old single-column page slow to type in.
 *
 * Unlike `SetupAccordion` (the wizard's own collapsible card, which has no
 * chevron because its pills already say whether a card needs attention), this
 * one is a plain toggle with nothing else telling the operator which way it
 * points, so the chevron carries that on its own -- turned by `aria-expanded`
 * rather than a second boolean class, so the two can never disagree. */
export function SettingsAccordion({
  title,
  open,
  onToggle,
  restartReason,
  children,
}: {
  title: string;
  open: boolean;
  onToggle: () => void;
  /** Why a swap cannot reach this section, when it cannot. The pill's hover
   * text, straight from the server's `frozen_paths`. */
  restartReason?: string;
  children: ReactNode;
}) {
  return (
    <section className="panel settings-accordion">
      <h2 className="settings-accordion-header">
        <button type="button" aria-expanded={open} onClick={onToggle}>
          <svg
            className="settings-accordion-chevron"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path d="M7 10l5 5 5-5z" />
          </svg>
          {title}
        </button>
        {restartReason !== undefined && (
          <span className="config-pill restart" title={restartReason}>
            restart to apply
          </span>
        )}
      </h2>
      {open && <div className="settings-accordion-body">{children}</div>}
    </section>
  );
}
