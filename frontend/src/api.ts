import type { PageResponse } from "./types";

async function asPageResponse(res: Response): Promise<PageResponse> {
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail ?? `Request failed: ${res.status}`);
  }
  return res.json();
}

export function startSession(objective: string): Promise<PageResponse> {
  return fetch("/api/learn/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ objective }),
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
