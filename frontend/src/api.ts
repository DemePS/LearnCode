import type { AskResponse, ClarifyResponse, ClarifyTurn, PageResponse } from "./types";

const TOKEN_STORAGE_KEY = "learncode.token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_STORAGE_KEY);
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Thrown specifically on a 401, so callers (App.tsx) can tell "not logged in / token expired"
 * apart from any other failure and send the learner back to the login form. */
export class AuthError extends Error {}

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    const message = detail.detail ?? `Request failed: ${res.status}`;
    if (res.status === 401) throw new AuthError(message);
    throw new Error(message);
  }
  return res.json();
}

const asPageResponse = asJson<PageResponse>;

interface AuthResponse {
  token: string;
  email: string;
}

export function register(email: string, password: string): Promise<AuthResponse> {
  return fetch("/api/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  }).then(asJson<AuthResponse>);
}

export function login(email: string, password: string): Promise<AuthResponse> {
  return fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  }).then(asJson<AuthResponse>);
}

export function clarify(objective: string, history: ClarifyTurn[]): Promise<ClarifyResponse> {
  return fetch("/api/learn/clarify", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ objective, history }),
  }).then(asJson<ClarifyResponse>);
}

export function startSession(objective: string, history: ClarifyTurn[] = []): Promise<PageResponse> {
  return fetch("/api/learn/start", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ objective, history }),
  }).then(asPageResponse);
}

export function sendAction(
  sessionId: string,
  action: string,
  payload: Record<string, unknown> = {},
): Promise<PageResponse> {
  return fetch(`/api/learn/${sessionId}/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ action, payload }),
  }).then(asPageResponse);
}

export function getSession(sessionId: string): Promise<PageResponse> {
  return fetch(`/api/learn/${sessionId}`, { headers: authHeaders() }).then(asPageResponse);
}

export function askQuestion(
  sessionId: string,
  question: string,
  context: Record<string, unknown> = {},
): Promise<AskResponse> {
  return fetch(`/api/learn/${sessionId}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ question, context }),
  }).then(asJson<AskResponse>);
}

/** Autosaves in-progress, unsubmitted code so it survives a refresh or a detour to another exercise
 * and back -- call this debounced (see App.tsx), not on every keystroke. Failures are swallowed by
 * the caller: losing one autosave tick is not worth surfacing an error over. */
export function saveDraft(sessionId: string, exerciseId: string, code: string): Promise<void> {
  return fetch(`/api/learn/${sessionId}/draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ exercise_id: exerciseId, code }),
  }).then(() => undefined);
}
