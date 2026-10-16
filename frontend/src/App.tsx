import { useEffect, useState } from "react";
import {
  ApiError,
  GITHUB_LOGIN_URL,
  clarify,
  getExercise,
  getMe,
  getRoadmap,
  getSession,
  listSessions,
  logout,
  sendAction,
  startSession,
} from "./api";
import { ActivityFeed } from "./components/ActivityFeed";
import { AskAI } from "./components/AskAI";
import { PageRenderer } from "./components/PageRenderer";
import { EMPTY_INDEX, indexRoadmap, type ExerciseIndex } from "./components/actionLabel";
import { ProjectList } from "./components/ProjectList";
import { QuestionPrompt } from "./components/QuestionPrompt";
import { navigate, routeForPath, type Route } from "./routes";
import type { ClarifyTurn, CodeEditorBlock, Me, Page } from "./types";

const SESSION_STORAGE_KEY = "learncode.session_id";
// Reserved action id the frontend's own "Back to roadmap" button always sends, regardless of what
// action ids the agent invented for its own buttons on the current page (see PAGE_SYSTEM_PROMPT).
const SHOW_ROADMAP_ACTION = "show_roadmap";

/** A fresh id per turn, so the activity feed polls the turn it is actually watching. `crypto` is
 * absent on an insecure origin, hence the fallback -- this id only has to be unique per turn. */
function newTurnId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** Why a request failed, in the words the learner needs. A 409 is not a fault: it means a turn for
 * this session is already running (a double click, or two tabs on the same project), so saying so is
 * more useful than the raw detail string -- and it must not clear the page they are looking at. */
function messageFor(err: unknown): string {
  if (err instanceof ApiError && err.status === 409) {
    return "A step is already running for this project -- wait for it to finish, then try again.";
  }
  return err instanceof Error ? err.message : String(err);
}

function App() {
  // null = still asking the server who this is; the three screens that follow (sign-in, loading,
  // app) are decided from it, so nothing else may render before it resolves.
  const [me, setMe] = useState<Me | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(() =>
    localStorage.getItem(SESSION_STORAGE_KEY),
  );
  const [page, setPage] = useState<Page | null>(null);
  const [objective, setObjective] = useState("");
  // Two separate busy states, because the two kinds of wait need different screens. `running` is a
  // *registered* turn (POST /start or /action): app/progress.py has an entry for it, so the activity
  // feed has something to poll. `clarifying` is the pre-session /clarify round-trip, which registers no
  // turn -- polling it would hammer an unknown turn id and show an empty feed, so it gets a plain
  // spinner instead. Together they mean "something is happening" (and gate the buttons).
  const [running, setRunning] = useState(false);
  const [clarifying, setClarifying] = useState(false);
  const busy = running || clarifying;
  const [error, setError] = useState<string | null>(null);
  const [resuming, setResuming] = useState(sessionId !== null);
  const [currentCode, setCurrentCode] = useState<{ exerciseId: string; fileName: string; code: string } | null>(
    null,
  );
  // The turn the activity feed watches, set as the request that starts that turn is issued -- never
  // afterwards. Setting it only once the response came back meant the feed mounted for zero frames:
  // React batches the "stop running" update with it, so the feed was never visible at all.
  const [turnId, setTurnId] = useState("");
  // The project list is a screen of its own, not a panel over the roadmap: it is where a learner picks
  // a project to go back to, so it replaces the page rather than covering it. It has a URL (/projects),
  // as every screen does, so a reload keeps the learner on it.
  const [route, setRoute] = useState<Route>(() => routeForPath(window.location.pathname));
  const showProjects = route.screen === "projects";
  // What the running turn is doing, for the feed's headline. "Building your tailored project" only fits
  // the roadmap turn; a submit grades the learner's own code against the exercise, and a retry reopens
  // it, so one fixed title over all of them described the wrong thing for every turn but the first.
  const [turnKind, setTurnKind] = useState<"design" | "grade" | "other">("design");
  // The roadmap's exercises, kept as the learner moves. Derived from `page` in one place rather than at
  // every setPage, because the pages that need it most (a feedback page) are the ones that carry no
  // roadmap: it is what lets "retry_ex_risk_gate" read as "Retry exercise 6" there.
  const [exerciseIndex, setExerciseIndex] = useState<ExerciseIndex>(EMPTY_INDEX);
  useEffect(() => {
    if (page) setExerciseIndex((previous) => indexRoadmap(page, previous));
  }, [page]);

  // Pre-session clarification: a lightweight back-and-forth (no project/session exists yet) that
  // happens before the first roadmap is designed, for an objective that's genuinely ambiguous.
  const [clarifyHistory, setClarifyHistory] = useState<ClarifyTurn[]>([]);
  const [clarifyQuestion, setClarifyQuestion] = useState<string | null>(null);

  // Who the browser is signed in as, before anything else: every other call needs the session cookie,
  // and asking for a project first would only bounce off a 401 we cannot distinguish from a failure.
  useEffect(() => {
    getMe()
      .then(setMe)
      .catch(() => setMe(null))
      .finally(() => setAuthChecked(true));
  }, []);

  /** Resume the project this browser was last on.
   *
   * localStorage is a hint, not a source of truth (it is per browser, and can outlive the session it
   * names), so an id it does not recognise falls through to the server's own list. A learner opening
   * the app on a second machine then still lands in their most recent project rather than on a blank
   * form. Only if there is nothing at all does the objective form appear. */
  useEffect(() => {
    if (!authChecked || !me) return;
    let cancelled = false;

    async function resume() {
      // A URL naming a project wins over localStorage: it is what a reload or a shared link asked for,
      // and it says which screen they were on, not just which project.
      const fromUrl = routeForPath(window.location.pathname);
      if (fromUrl.screen === "project" || fromUrl.screen === "roadmap" || fromUrl.screen === "exercise") {
        try {
          const res =
            fromUrl.screen === "exercise"
              ? await getExercise(fromUrl.sessionId, fromUrl.exerciseId)
              : fromUrl.screen === "roadmap"
                ? await getRoadmap(fromUrl.sessionId)
                : await getSession(fromUrl.sessionId);
          if (cancelled) return;
          localStorage.setItem(SESSION_STORAGE_KEY, fromUrl.sessionId);
          setSessionId(fromUrl.sessionId);
          setPage(res.page);
          return;
        } catch {
          // Gone, someone else's, or no such exercise: fall through to the list below. The URL is
          // corrected there rather than left pointing at a project that cannot be shown.
        }
      }
      const stored = localStorage.getItem(SESSION_STORAGE_KEY);
      if (stored) {
        try {
          const res = await getSession(stored);
          if (cancelled) return;
          setSessionId(stored);
          setPage(res.page);
          return;
        } catch {
          // A 404 (gone, or someone else's), a 409 etc. -- fall through to the list below.
          localStorage.removeItem(SESSION_STORAGE_KEY);
        }
      }
      try {
        const { sessions } = await listSessions();
        if (cancelled) return;
        const newest = sessions[0];
        if (newest) {
          const res = await getSession(newest.id);
          if (cancelled) return;
          localStorage.setItem(SESSION_STORAGE_KEY, newest.id);
          setSessionId(newest.id);
          setPage(res.page);
          return;
        }
      } catch {
        // No sessions (or the list could not be read): show the objective form.
      }
      if (!cancelled) {
        setSessionId(null);
        setPage(null);
      }
    }

    resume()
      .catch((err) => {
        if (!cancelled) setError(messageFor(err));
      })
      .finally(() => {
        if (!cancelled) setResuming(false);
      });
    return () => {
      cancelled = true;
    };
  }, [authChecked, me]);

  // Back/forward. The browser already changed the URL, so this only has to agree with it: the screen
  // follows from `route`, and the page follows from which project the URL names.
  useEffect(() => {
    function onPop() {
      const next = routeForPath(window.location.pathname);
      setRoute(next);
      if (next.screen !== "projects" && next.screen !== "objective") void openFromRoute(next);
    }
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Open whatever a route names, reading pages from the database. No turn: every route into a
   * project is a GET, which is the point of the per-screen paths. */
  async function openFromRoute(next: Route) {
    if (next.screen === "objective" || next.screen === "projects") return;
    setResuming(true);
    setError(null);
    try {
      const res =
        next.screen === "exercise"
          ? await getExercise(next.sessionId, next.exerciseId)
          : next.screen === "roadmap"
            ? await getRoadmap(next.sessionId)
            : await getSession(next.sessionId);
      localStorage.setItem(SESSION_STORAGE_KEY, next.sessionId);
      setSessionId(next.sessionId);
      setPage(res.page);
    } catch (err) {
      // A URL that names a project this learner cannot see: say so on the objective form rather than
      // leaving the address bar pointing at a screen that will never render.
      setError(messageFor(err));
      setPage(null);
      setSessionId(null);
      navigate({ screen: "objective" }, true);
      setRoute({ screen: "objective" });
    } finally {
      setResuming(false);
    }
  }

  /** Remember which screen a project's page belongs at, so a reload reopens the same one. A page with
   * a code editor is an exercise; a page with a roadmap is the roadmap; anything else (a feedback
   * page) is the project's newest page. */
  function routeForPage(current: Page, id: string): Route {
    const editor = current.blocks.find((b): b is CodeEditorBlock => b.type === "code_editor");
    if (editor) return { screen: "exercise", sessionId: id, exerciseId: editor.exercise_id };
    if (current.blocks.some((b) => b.type === "roadmap")) return { screen: "roadmap", sessionId: id };
    return { screen: "project", sessionId: id };
  }

  async function proceed(obj: string, history: ClarifyTurn[], turn: string, needsClarify: boolean) {
    // The clarify call is skipped entirely once a question has been asked and answered: the learner's
    // answer is carried in `history`, which the roadmap turn reads (routes.py), and re-asking would
    // spend a whole extra model round-trip only to be told what we already know.
    if (needsClarify) {
      setClarifying(true);
      try {
        const res = await clarify(obj, history);
        if (!res.ready) {
          // A question for the learner: stop here and let the clarify screen render (the caller shows it
          // from clarifyQuestion). No turn was started, so no feed was ever mounted.
          setClarifyQuestion(res.question);
          return;
        }
        setClarifyQuestion(null);
      } finally {
        setClarifying(false);
      }
    }
    // Set *before* the request goes out, so the feed is mounted while the turn actually runs. The id is
    // the one /start is being called with, which is the turn progress.start records server-side.
    setTurnId(turn);
    setRunning(true);
    try {
      const started = await startSession(obj, history, turn);
      localStorage.setItem(SESSION_STORAGE_KEY, started.session_id);
      setSessionId(started.session_id);
      setPage(started.page);
      setCurrentCode(null);
      // A new project starts on its roadmap, and the URL says so -- so a reload lands there rather
      // than on a bare /projects.
      const next = routeForPage(started.page, started.session_id);
      navigate(next, true);
      setRoute(next);
    } finally {
      setRunning(false);
    }
  }

  async function handleStart(e: React.FormEvent) {
    e.preventDefault();
    if (!objective.trim() || busy) return;
    setError(null);
    setClarifyHistory([]);
    try {
      // The id is minted per attempt and handed straight to /start -- it is not shown until that call is
      // issued, so the feed can never be pointed at a turn that does not exist (see proceed).
      await proceed(objective.trim(), [], newTurnId(), true);
    } catch (err) {
      setError(messageFor(err));
    }
  }

  async function handleClarifyAnswer(answer: string) {
    if (!clarifyQuestion || busy) return;
    setError(null);
    const history = [...clarifyHistory, { question: clarifyQuestion, answer }];
    setClarifyHistory(history);
    try {
      // No further clarify call: the answer is in `history`, which /start passes to the roadmap turn.
      await proceed(objective.trim(), history, newTurnId(), false);
    } catch (err) {
      setError(messageFor(err));
    }
  }

  async function handleAction(action: string, payload: Record<string, unknown> = {}) {
    if (!sessionId || busy) return;
    // Most actions are answered from the database -- opening an exercise and going back to the roadmap
    // never run a turn -- so the feed is mounted only when the page we are looking at says this click is
    // the one that grades: the code editor's own submit_action. Showing it for every click left the feed
    // polling an unknown turn and hiding the page until the answer arrived. When the page cannot tell
    // (no code editor on it), it is shown, since a turn is then the likely outcome.
    const editor = page?.blocks.find((b) => b.type === "code_editor") as CodeEditorBlock | undefined;
    const graded = !editor || action === editor.submit_action;
    // One id, used for both the feed and the request: if they differed, the poll would watch a turn the
    // server never recorded. It is set before the await, because setting it after the response would
    // batch it with setRunning(false) and the feed would never be visible.
    const turn = graded ? newTurnId() : "";
    setTurnId(turn);
    if (graded) {
      // A submit is graded; a retry is served from the database by /action's own routing (so it never
      // costs a turn), and anything else is the model doing something we cannot name -- which gets the
      // neutral wording rather than a claim about what it is doing.
      setTurnKind(editor && action === editor.submit_action ? "grade" : "other");
      setRunning(true);
    }
    setError(null);
    try {
      const res = await sendAction(sessionId, action, payload, turn);
      setPage(res.page);
      // The URL follows the page: a feedback page is not a screen of its own, so grading keeps the
      // learner at the exercise's URL, and any deeper navigation they make is a real URL.
      const next = routeForPage(res.page, sessionId);
      if (next.screen !== "exercise") {
        navigate(next);
        setRoute(next);
      }
      // Deliberately not clearing currentCode: the learner's submission is what they just handed in, and
      // the feedback page only makes sense next to it (the help widget should still be able to talk about
      // the code they are being graded on). It is replaced as soon as they type again, or when another
      // page's editor mounts.
    } catch (err) {
      setError(messageFor(err));
    } finally {
      setRunning(false);
    }
  }

  /** Leave the project list for the project this browser was on, without re-fetching the page it
   * already has in memory. */
  function backToProject() {
    if (!sessionId || !page) {
      startOver();
      return;
    }
    const next = routeForPage(page, sessionId);
    navigate(next);
    setRoute(next);
  }

  function startOver() {    localStorage.removeItem(SESSION_STORAGE_KEY);
    setSessionId(null);
    setPage(null);
    setObjective("");
    setError(null);
    setCurrentCode(null);
    setClarifyHistory([]);
    setClarifyQuestion(null);
    navigate({ screen: "objective" });
    setRoute({ screen: "objective" });
  }

  /** Open a project from the list. The pages are read back rather than regenerated, so this is one
   * request and no turn: nothing here is busy, and the list is left behind only once the pages are in
   * hand (a failure keeps the learner on the list, with the reason, instead of on a blank screen). */
  async function openProject(id: string) {
    setResuming(true);
    setError(null);
    try {
      const res = await getSession(id);
      localStorage.setItem(SESSION_STORAGE_KEY, id);
      setSessionId(id);
      setPage(res.page);
      setCurrentCode(null);
      setClarifyHistory([]);
      setClarifyQuestion(null);
      const next = routeForPage(res.page, id);
      navigate(next);
      setRoute(next);
    } catch (err) {
      setError(messageFor(err));
    } finally {
      setResuming(false);
    }
  }

  async function handleSignOut() {
    try {
      await logout();
    } finally {
      // A reload rather than resetting the state above: the cookie is gone either way, so anything
      // still in memory belongs to a learner who is no longer signed in.
      window.location.reload();
    }
  }

  if (!authChecked) {
    return <div className="shell shell-centered">Loading…</div>;
  }

  if (!me) {
    return (
      <div className="shell shell-centered">
        <div className="signin">
          <h1>LearnCode</h1>
          <p>
            Learn to build real projects, one exercise at a time. Sign in so your projects follow you
            between machines -- we only ask GitHub who you are.
          </p>
          {/* A full navigation, not a fetch: the browser must follow the OAuth redirect to GitHub. */}
          <a className="btn btn-primary" href={GITHUB_LOGIN_URL}>
            Sign in with GitHub
          </a>
        </div>
      </div>
    );
  }

  if (resuming) {
    return <div className="shell shell-centered">Loading your session…</div>;
  }

  if (showProjects) {
    return (
      <div className="shell">
        <header className="topbar">
          <span className="brand">LearnCode</span>
          <div className="topbar-actions">
            {/* Only offered when there is a project to go back to: with none, the list is already the
                whole screen and the button would point at nothing. */}
            {(sessionId || page) && (
              <button className="btn btn-link" onClick={backToProject}>
                Back to project
              </button>
            )}
            <span className="user">
              {me.avatar_url && <img className="avatar" src={me.avatar_url} alt="" />}
              <span className="user-login">{me.login}</span>
            </span>
            <button className="btn btn-link" onClick={handleSignOut}>
              Sign out
            </button>
          </div>
        </header>
        <main>
          <ProjectList
            currentSessionId={sessionId}
            onOpen={openProject}
            onNew={() => {
              startOver();
            }}
          />
        </main>
      </div>
    );
  }

  // What the feed says it is doing. Both lines must agree with what the turn actually is: the backend
  // sends its own opening line for the feed ("Reviewing your code" for a submission), so a headline
  // claiming something different above it would read as two unrelated processes.
  const turnTitle =
    turnKind === "grade"
      ? "Evaluating your submission"
      : turnKind === "other"
        ? "Working on it"
        : "Building your tailored project";

  if (!sessionId || !page) {
    // The first turn is two waits (clarify, then /start), and they get different screens. `running` is
    // the /start turn, which app/progress.py has registered, so the feed has steps to poll -- and it is
    // shown for as long as that request is in flight, which is the longest wait in the app. `clarifying`
    // registers no turn, so polling there would hit an unknown id and show an empty feed: it gets a
    // plain spinner instead.
    if (running) {
      return (
        <div className="shell shell-centered">
          <ActivityFeed turnId={turnId} active={running} />
        </div>
      );
    }
    if (clarifying) {
      return <div className="shell shell-centered">Thinking…</div>;
    }
    return (
      <div className="shell shell-centered">
        {clarifyQuestion ? (
          <QuestionPrompt question={clarifyQuestion} onAnswer={handleClarifyAnswer} submitting={busy} />
        ) : (
          <form className="objective-form" onSubmit={handleStart}>
            <h1>What do you want to learn to build?</h1>
            <p className="objective-hint">
              One sentence is enough -- e.g. "build a URL shortener in Python" or "learn React by
              building a Kanban board". An agent will design a real, GitHub-grounded project and
              break it into exercises you build one component at a time -- it may ask a quick
              clarifying question first if your objective leaves something important open.
            </p>
            <textarea
              value={objective}
              onChange={(e) => setObjective(e.target.value)}
              placeholder="I want to learn..."
              rows={3}
              disabled={busy}
            />
            <button className="btn btn-primary" type="submit" disabled={busy || !objective.trim()}>
              {busy ? "Thinking…" : "Start"}
            </button>
            {/* The form is the only screen a learner with no project ever sees, so this is the way in
                to the list: without it, "My projects" lives inside a project they do not have yet. */}
            <button type="button" className="btn btn-link" onClick={() => { navigate({ screen: "projects" }); setRoute({ screen: "projects" }); }}>
              My projects
            </button>
            {error && <p className="error">{error}</p>}
          </form>
        )}
      </div>
    );
  }

  // "Back to roadmap" is a frontend-owned control, not something the agent has to remember to add
  // to its own actions block -- that was fragile (the agent sometimes left it off a page entirely,
  // deterministically, not just occasionally). It's hidden only when the current page already shows
  // the roadmap itself, and always sends the same reserved action id (see PAGE_SYSTEM_PROMPT) no
  // matter what action ids the agent invented for its own buttons on this page.
  const onRoadmap = page.blocks.some((b) => b.type === "roadmap");

  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">LearnCode</span>
        <div className="topbar-actions">
          {!onRoadmap && (
            <button className="btn btn-link" onClick={() => handleAction(SHOW_ROADMAP_ACTION)} disabled={busy}>
              Back to roadmap
            </button>
          )}
          <button className="btn btn-link" onClick={() => { navigate({ screen: "projects" }); setRoute({ screen: "projects" }); }} disabled={busy}>
            My projects
          </button>
          <button className="btn btn-link" onClick={startOver}>
            Start a new project
          </button>
          <span className="user">
            {me.avatar_url && <img className="avatar" src={me.avatar_url} alt="" />}
            <span className="user-login">{me.login}</span>
          </span>
          <button className="btn btn-link" onClick={handleSignOut}>
            Sign out
          </button>
        </div>
      </header>
      <main>
        {error && <p className="error">{error}</p>}
        {/* While a *registered* turn runs the feed takes the page's place rather than sitting above it:
            the turn is going to replace this whole screen with a new page in under a minute, so there
            is nothing worth reading underneath, and the one thing the learner needs is to see it is
            still going. `running` (not busy) is the flag that means a turn exists to poll -- the feed
            must not be mounted for a clarify round-trip, which registers nothing in app/progress.py. */}
        {running ? (
          <ActivityFeed turnId={turnId} active={running} title={turnTitle} />
        ) : (
          <PageRenderer
            page={page}
            onAction={handleAction}
            submitting={busy}
            index={exerciseIndex}
            onCodeChange={(exerciseId, fileName, code) => setCurrentCode({ exerciseId, fileName, code })}
          />
        )}
      </main>
      {!busy && <AskAI sessionId={sessionId} currentCode={currentCode} />}
    </div>
  );
}

export default App;
