/* The one Node builtin a test file needs, declared here rather than pulled in
 * with `@types/node`.
 *
 * `src/styles.test.ts` reads the stylesheets off disk as text -- see that file
 * for why it cannot import them. Nothing else in this application touches a
 * Node API: it is a browser bundle, and `tsc --noEmit` covering the tests is
 * what surfaced the gap. Adding `@types/node` to a pinned dependency set to
 * type one call is a worse trade than eight lines here; if a second caller
 * ever appears, add the real package and delete this file.
 */
declare module "node:fs" {
  export function readFileSync(path: URL | string, encoding: "utf8"): string;
}
