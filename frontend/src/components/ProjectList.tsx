import { useCallback, useEffect, useState } from "react";
import { deleteSession, listSessions } from "../api";
import type { SessionSummary } from "../types";

/** Formats a timestamp the way the list needs it: how long ago, which is what tells a learner which
 * project they were last in. Falls back to the date when it is older than a week, where "34 days ago"
 * stops being useful. */
function ago(iso: string): string {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";
  const seconds = Math.round((Date.now() - then.getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days} day${days === 1 ? "" : "s"} ago`;
  return then.toLocaleDateString();
}

/** Every project this learner has, and the only place one can be deleted.
 *
 * The list is read fresh on mount rather than kept in App: a project's objective and its "last worked
 * on" both change while the learner is inside it, so a copy held from the last visit would be stale
 * exactly when it matters. Deleting is confirmed here (a browser dialog would be styled by the browser
 * and easy to dismiss by accident) and, once confirmed, is immediate and permanent -- which is why the
 * confirmation names the project rather than asking a bare "are you sure". */
export function ProjectList({
  currentSessionId,
  onOpen,
  onNew,
}: {
  currentSessionId: string | null;
  onOpen: (id: string) => void;
  onNew: () => void;
}) {
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  const load = useCallback(() => {
    listSessions()
      .then((res) => setSessions(res.sessions))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(load, [load]);

  async function handleDelete(id: string) {
    setDeleting(id);
    setError(null);
    try {
      await deleteSession(id);
      // Dropped locally rather than refetched: the list on screen is already what the server would
      // return, and a refetch would flash a spinner over a list the learner is reading.
      setSessions((rows) => (rows ?? []).filter((r) => r.id !== id));
      setConfirming(null);
    } catch (err) {
      // A 409 (a turn is still running) is the one that must not look like a broken page: the project
      // is still there, and deleting it again once the turn finishes works.
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeleting(null);
    }
  }

  return (
    <div className="projects">
      <div className="projects-head">
        <h1>Your projects</h1>
        <button className="btn btn-primary" onClick={onNew}>
          Start a new project
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {sessions === null && <p className="projects-empty">Loading…</p>}
      {sessions?.length === 0 && (
        <p className="projects-empty">
          Nothing here yet -- start a project and it will show up in this list.
        </p>
      )}
      <ul className="project-list">
        {(sessions ?? []).map((s) => (
          <li key={s.id} className="project-row">
            <button className="project-open" onClick={() => onOpen(s.id)}>
              <span className="project-objective">{s.objective}</span>
              <span className="project-meta">
                {ago(s.updated_at)}
                {s.id === currentSessionId && " · open now"}
              </span>
            </button>
            {confirming === s.id ? (
              <span className="project-confirm">
                <span className="project-confirm-text">Delete this project and its clones?</span>
                <button
                  className="btn btn-link btn-danger"
                  onClick={() => handleDelete(s.id)}
                  disabled={deleting === s.id}
                >
                  {deleting === s.id ? "Deleting…" : "Delete"}
                </button>
                <button className="btn btn-link" onClick={() => setConfirming(null)} disabled={deleting === s.id}>
                  Cancel
                </button>
              </span>
            ) : (
              <button
                className="btn btn-link btn-danger"
                onClick={() => {
                  setError(null);
                  setConfirming(s.id);
                }}
              >
                Delete
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
