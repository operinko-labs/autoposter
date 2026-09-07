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

/** Every step the wizard has, in order; `visibleSteps` is this list less the
 * ones a given deployment does not need. */
const ALL_STEPS: StepId[] = ["password", "url", "database", "systems", "finish"];

/** Which steps this deployment has at all.
 *
 * The database step is the only conditional one, and the condition is the
 * SOURCE the server reports rather than the boolean beside it. `database` is
 * true for a URL the environment supplies AND for one this wizard staged a
 * moment ago -- the step's own submit makes it true -- so keying on it deleted
 * the step at the instant it was answered: Back landed on the address pane,
 * and the step's `Stored` pill and its empty-means-keep were correct and
 * unreachable. Only `"resolved"` hides it, which is the shape that must never
 * be asked (the wizard cannot improve on a value the boot resolver already
 * holds, and asking implies it can).
 *
 * The URL step is NOT conditional. A deployment whose config document already
 * resolves still needs the address for the *arr registration; it simply is not
 * persisted, which the finish page says by name (facts C1).
 */
export function visibleSteps(progress: SetupProgress | null): StepId[] {
  if (progress === null) return ["password"];
  return ALL_STEPS.filter(
    (step) => step !== "database" || progress.database_source !== "resolved",
  );
}

/** The step to land on once `step` has been accepted, given the progress the
 * server answered AFTER that submit.
 *
 * The progress matters, and it must be the post-submit one. Before the
 * password is accepted the wizard knows only `["password"]`, so a next step
 * read from that order can only clamp to the pane it is already on -- which is
 * what made the first advance need an effect of its own to supply it.
 *
 * `step` may not be in the order at all. No step leaves it by being answered
 * any more -- that was the defect `database_source` closed -- but the order is
 * the server's answer, so the search runs over the full list and returns the
 * first step that is still visible rather than indexing into the order and
 * walking one place forward, which would have to be right about the order
 * twice.
 */
export function stepAfter(step: StepId, progress: SetupProgress | null): StepId {
  const order = visibleSteps(progress);
  for (const candidate of ALL_STEPS.slice(ALL_STEPS.indexOf(step) + 1)) {
    if (order.includes(candidate)) return candidate;
  }
  return order[order.length - 1];
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
 * than by a 400 the operator would have to read.
 *
 * A candidate this deployment does not have at all is refused first: `indexOf`
 * answers -1 for it, and -1 is `<=` every index, so the comparison alone
 * called an absent step reachable and only the page's own `order.includes`
 * beside it kept that off screen. */
export function canNavigate(
  candidate: StepId,
  progress: SetupProgress | null,
): boolean {
  const order = visibleSteps(progress);
  const index = order.indexOf(candidate);
  return index !== -1 && index <= order.indexOf(farthestStep(progress));
}
