import type { AskResponse, ClarifyResponse, ClarifyTurn, PageResponse } from "./types";

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

export function startSession(objective: string, history: ClarifyTurn[] = []): Promise<PageResponse> {
  return fetch("/api/learn/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, payload }),
  }).then(asPageResponse);
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
