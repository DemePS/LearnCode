import { useEffect, useRef, useState } from "react";
import { getProgress } from "../api";
import type { ProgressStep } from "../types";

/** The full-screen "a turn is running" view.
 *
 * A turn that grounds its roadmap in a real repository takes 20-60 seconds -- a GitHub search, a git
 * clone, then dozens of file reads -- and until this existed the learner watched a disabled button
 * for that whole time with no way to tell work from a hang. This takes over the screen for the
 * duration and says what the agent is actually doing.
 *
 * One line at a time, deliberately. The agent makes 40+ tool calls in a single turn, and a growing
 * list of them reads as a log to scroll rather than a status to glance at: it grows taller, the
 * current step drifts, and the movement is "the list shifted" instead of "something changed". A
 * single line that is *replaced* keeps the layout still, so every change is unambiguously news.
 *
 * Polling rather than a stream: the turn is a single ordinary POST whose response is the finished
 * page, so there is no stream to attach to, and a poll every 600ms on a local API costs nothing.
 */
const POLL_MS = 600;

/** Where steps come from. Defaults to the live API; the preview page (and any future test) injects
 * its own source instead of mocking fetch. */
export type ProgressSource = (turnId: string) => Promise<{ steps: ProgressStep[]; done: boolean }>;

interface Props {
  /** Identifies the turn to poll. Empty means nothing to show. */
  turnId: string;
  /** Whether a turn is currently running. Polling stops as soon as it is false. */
  active: boolean;
  /** Headline for the turn being run. Defaults to the roadmap wording, which is the common case. */
  title?: string;
  /** Defaults to the real endpoint. */
  source?: ProgressSource;
}

export function ActivityFeed({ turnId, active, title = "Building your tailored project", source = getProgress }: Props) {
  const [steps, setSteps] = useState<ProgressStep[]>([]);
  // Guards against a late response from a previous turn overwriting the current one's feed.
  const current = useRef(turnId);

  useEffect(() => {
    current.current = turnId;
    setSteps([]);
    if (!active || !turnId) return;

    let cancelled = false;
    const tick = async () => {
      try {
        const res = await source(turnId);
        if (cancelled || current.current !== turnId) return;
        setSteps(res.steps);
      } catch {
        // A failed poll is not worth showing: the turn's own request reports real errors, and the
        // next tick will very likely succeed.
      }
    };
    // Polled rather than streamed: the turn is a single ordinary POST whose response is the finished
    // page, so there is no stream to attach to, and a poll every 600ms on a local API costs nothing.
    const poll = window.setInterval(tick, POLL_MS);
    void tick();
    return () => {
      cancelled = true;
      window.clearInterval(poll);
    };
  }, [turnId, active, source]);

  if (!active || !turnId) return null;

  // One line, and only the most recent step: the agent makes 40+ tool calls in a turn, and a growing
  // list reads as a log to scroll rather than a status to glance at. A single line that is *replaced*
  // keeps the layout still, so each change is unambiguously news.
  //
  // Nothing here distinguishes a step that worked from one that failed, on purpose: the agent is
  // written to carry on when a search, a clone or a page open fails (that is why every tool is
  // offered), so a failure is something it has already handled, and showing it would ask the learner
  // to worry about a problem that no longer exists. The next line simply says what it did next.
  const last = steps.length ? steps[steps.length - 1] : undefined;
  const text = last ? `${last.text}${last.detail ? ` ${last.detail}` : ""}` : "Getting started…";

  return (
    <div className="working" role="status" aria-live="polite">
      <div className="working-spinner" aria-hidden="true" />
      <h2 className="working-title">{title}</h2>
      <p className="working-step">{text}</p>
    </div>
  );
}
