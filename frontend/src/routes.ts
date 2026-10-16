/** The app's URLs.
 *
 * Each screen the learner can be on has a real path, so a reload keeps them where they are and the
 * back button works. Plain History API rather than a router dependency: there are four paths, and
 * they are all "one project, one screen" -- the state that decides what to show (which page) already
 * lives in App, so a router would only add a second copy of it.
 */

export type Route =
  | { screen: "objective" }
  | { screen: "projects" }
  | { screen: "project"; sessionId: string }
  | { screen: "roadmap"; sessionId: string }
  | { screen: "exercise"; sessionId: string; exerciseId: string };

const BASE = "/projects";

/** The asset paths Vite serves; a path starting with these is not a route. */
const STATIC_PREFIXES = ["/assets/", "/api/", "/favicon"];

export function pathForRoute(route: Route): string {
  switch (route.screen) {
    case "objective":
      return "/";
    case "projects":
      return BASE;
    case "project":
      return `${BASE}/${route.sessionId}`;
    case "roadmap":
      return `${BASE}/${route.sessionId}/roadmap`;
    case "exercise":
      return `${BASE}/${route.sessionId}/exercises/${route.exerciseId}`;
  }
}

/** The route a path names. Anything unrecognised is the objective form, which is the one screen that
 * needs no project and cannot fail to render. */
export function routeForPath(pathname: string): Route {
  if (STATIC_PREFIXES.some((p) => pathname.startsWith(p))) return { screen: "objective" };
  const parts = pathname.replace(/\/+$/, "").split("/").filter(Boolean);
  if (parts[0] !== "projects") return { screen: "objective" };
  if (parts.length === 1) return { screen: "projects" };
  const sessionId = parts[1];
  if (parts.length === 2) return { screen: "project", sessionId };
  if (parts.length === 3 && parts[2] === "roadmap") return { screen: "roadmap", sessionId };
  if (parts.length === 4 && parts[2] === "exercises") {
    return { screen: "exercise", sessionId, exerciseId: decodeURIComponent(parts[3]) };
  }
  return { screen: "objective" };
}

/** Move to a route. `replace` is for corrections the learner did not navigate to (resuming a
 * project, a deleted one), so they do not have to press back through them. */
export function navigate(route: Route, replace = false): void {
  const path = pathForRoute(route);
  if (replace) window.history.replaceState(null, "", path);
  else window.history.pushState(null, "", path);
}
