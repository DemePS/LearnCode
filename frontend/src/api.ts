import type {
  AskResponse,
  ClarifyResponse,
  ClarifyTurn,
  Me,
  PageResponse,
  ProgressResponse,
  SessionListResponse,
  ExerciseListResponse,
} from "./types";

/** A failed API call, carrying the HTTP status so callers can tell the cases apart that need
 * different handling: 401 means "not signed in" (show the sign-in screen), 409 means "a turn is
 * already running for this session" (a short message, not an error screen -- nothing is broken). */
export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, detail.detail ?? `Request failed: ${res.status}`);
  }
  return res.json();
}

const asPageResponse = asJson<PageResponse>;

export function clarify(objective: string, history: ClarifyTurn[]): Promise<ClarifyResponse> {
  return fetch("/api/learn/clarify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ objective, history }),
  }).then(asJson<ClarifyResponse>);
}

export function startSession(
  objective: string,
  history: ClarifyTurn[] = [],
  turnId = "",
): Promise<PageResponse> {
  return fetch("/api/learn/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ objective, history, turn_id: turnId }),
  }).then(asPageResponse);
}

export function sendAction(
  sessionId: string,
  action: string,
  payload: Record<string, unknown> = {},
  turnId = "",
): Promise<PageResponse> {
  return fetch(`/api/learn/${sessionId}/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, payload, turn_id: turnId }),
  }).then(asPageResponse);
}

/** The live feed for a turn that is still running. Never throws on an unknown turn id -- the backend
 * answers those with an empty, finished feed, since a poll can arrive just after a turn ended. */
export function getProgress(turnId: string): Promise<ProgressResponse> {
  return fetch(`/api/learn/progress/${encodeURIComponent(turnId)}`).then(asJson<ProgressResponse>);
}

export function getSession(sessionId: string): Promise<PageResponse> {
  return fetch(`/api/learn/${sessionId}`).then(asPageResponse);
}

/** The stored roadmap, whatever screen the learner is on. A GET, so asking for a screen the agent
 * already produced can never reach the model. */
export function getRoadmap(sessionId: string): Promise<PageResponse> {
  return fetch(`/api/learn/${sessionId}/roadmap`).then(asPageResponse);
}

/** This project's exercises, in roadmap order -- read from stored pages, no turn. */
export function listExercises(sessionId: string): Promise<ExerciseListResponse> {
  return fetch(`/api/learn/${sessionId}/exercises`).then(asJson<ExerciseListResponse>);
}

/** One stored exercise, draft included. Opening an exercise is a plain read. */
export function getExercise(sessionId: string, exerciseId: string): Promise<PageResponse> {
  return fetch(`/api/learn/${sessionId}/exercises/${encodeURIComponent(exerciseId)}`).then(
    asPageResponse,
  );
}

/** The caller's own sessions, newest first -- what lets a reload resume the most recent project when
 * localStorage no longer names one (cleared, or from another browser). */
export function listSessions(): Promise<SessionListResponse> {
  return fetch("/api/learn").then(asJson<SessionListResponse>);
}

/** Forget one project: its pages, its session and its clones. Throws a 409 ApiError while a step is
 * still running for it, which the caller should say out loud rather than retry. */
export function deleteSession(sessionId: string): Promise<{ ok: boolean }> {
  return fetch(`/api/learn/${sessionId}`, { method: "DELETE" }).then(asJson<{ ok: boolean }>);
}

/** Who this browser is signed in as; throws an ApiError with status 401 when it is not. */
export function getMe(): Promise<Me> {
  return fetch("/api/auth/me").then(asJson<Me>);
}

/** Drop the server-side session (the cookie alone is useless), then the caller reloads. */
export function logout(): Promise<{ ok: boolean }> {
  return fetch("/api/auth/logout", { method: "POST" }).then(asJson<{ ok: boolean }>);
}

/** Where "Sign in with GitHub" points. A plain navigation, not a fetch: the browser has to follow the
 * 302 all the way to github.com for the learner to see GitHub's own consent screen. */
export const GITHUB_LOGIN_URL = "/api/auth/github/login";

export function askQuestion(
  sessionId: string,
  question: string,
  context: Record<string, unknown> = {},
): Promise<AskResponse> {
  return fetch(`/api/learn/${sessionId}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, context }),
  }).then(asJson<AskResponse>);
}
