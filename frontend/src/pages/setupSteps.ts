/** The wizard's step algebra, with no React in it.
 *
 * v1 had no forward navigation and therefore no back: `Setup.tsx` rendered
 * every unfinished pane at once and derived a step NUMBER from progress for a
 * decorative rail. v2's operator moves one step at a time and can go back, so
 * "which pane" becomes real page state -- and the rule that keeps it honest is
 * that the SERVER still owns how far forward that state may go.
 *
 * Pure, and exported separately from the page, so the rule can be tested
 * without rendering anything.
 */
import type { SetupProgress } from "../api/setup";

export type StepId = "password" | "url" | "database" | "systems" | "finish";

/** The rail's labels, in order. */
export const STEP_LABELS: Record<StepId, string> = {
  password: "Password",
  url: "Address",
  database: "Database",
  systems: "Systems",
  finish: "Finish",
};

/** Which steps this deployment has at all.
 *
 * The database step is the only conditional one, and the condition is the
 * server's: `progress.database` is true when a URL resolves from the
 * environment or the state file, which is the deployment shape that must never
 * be asked for one (the wizard cannot improve on a value the environment
 * already supplies, and asking implies it can).
 *
 * The URL step is NOT conditional. A deployment whose config document already
 * resolves still needs the address for the *arr registration; it simply is not
 * persisted, which the finish page says by name (facts C1).
 */
export function visibleSteps(progress: SetupProgress | null): StepId[] {
  if (progress === null) return ["password"];
  const steps: StepId[] = ["password", "url"];
  if (!progress.database) steps.push("database");
  steps.push("systems", "finish");
  return steps;
}

/** The furthest step the server says is reachable.
 *
 * Forward is gated: a step becomes reachable only once the one before it has
 * been accepted by a 200. Back is free -- `current` may be set to anything at
 * or before this -- so no "unstage" endpoint exists and there is nothing about
 * back navigation that can put the server and the page out of step.
 */
export function farthestStep(progress: SetupProgress | null): StepId {
  if (progress === null) return "password";
  if (!progress.public_url) return "url";
  if (!progress.database) return "database";
  if (progress.required.length > 0 || progress.config_source === null) return "systems";
  return "finish";
}

/** Whether `candidate` may be navigated to, given how far the server says the
 * wizard has got. Back is `<=`; forward past `farthest` is refused here rather
 * than by a 400 the operator would have to read. */
export function canNavigate(
  candidate: StepId,
  progress: SetupProgress | null,
): boolean {
  const order = visibleSteps(progress);
  return order.indexOf(candidate) <= order.indexOf(farthestStep(progress));
}
