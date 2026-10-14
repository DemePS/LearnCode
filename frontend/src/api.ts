import type { AskResponse, ClarifyResponse, ClarifyTurn, PageResponse, ProgressResponse } from "./types";

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail ?? `Request failed: ${res.status}`);
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
