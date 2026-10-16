/** The page spec seedagent returns on every turn (see backend/app/agent.py's PAGE_SYSTEM_PROMPT).
 * The backend never shapes this itself, so block fields are typed loosely (the agent is a well
 * instructed but not type-checked source) and PageRenderer must cope with a block missing a field
 * it expected. */

export type BlockStatus = "locked" | "available" | "in_progress" | "done";

export interface MarkdownBlock {
  type: "markdown";
  content: string;
}

export interface StatRowBlock {
  type: "stat_row";
  stats: { label: string; value: string }[];
}

export interface RoadmapItem {
  id: string;
  title: string;
  summary: string;
  /** Informational only -- progress and recommended order, never a gate. Every item opens via
   * open_action regardless of status; the learner can work through the project in any order. */
  status: BlockStatus;
  open_action: string;
}

export interface RoadmapBlock {
  type: "roadmap";
  items: RoadmapItem[];
}

export interface CodeEditorBlock {
  type: "code_editor";
  exercise_id: string;
  file_name: string;
  language: string;
  instructions: string;
  starter_code: string;
  submit_action: string;
  /** The learner's own code, written here on submit. Absent until they submit, and it is what
   * re-opening the exercise shows instead of the starter code. It lives on the block, where the
   * editor that owns it reads it -- not on the page. */
  draft?: ExerciseDraft;
}

/** The learner's own code, stored on the exercise page. Null until they submit, and it is what
 * re-opening an exercise shows instead of the starter code. */
export type ExerciseDraft = string | null | undefined;

export interface FeedbackBlock {
  type: "feedback";
  passed: boolean;
  summary: string;
  details: string;
}

export interface ActionItem {
  /** The id the agent invented and gets back verbatim. The button's wording is derived from this
   * (see components/actionLabel.ts) -- `label` is only present on pages the agent wrote before it
   * stopped sending one, and is ignored. */
  action: string;
  label?: string;
  style?: "primary" | "secondary";
}

export interface ActionsBlock {
  type: "actions";
  items: ActionItem[];
}

/** A block the agent produced that this frontend doesn't (yet) know how to render -- skipped, not
 * fatal, by PageRenderer's default case. Kept out of the Block union below so TypeScript can still
 * narrow on `type` for the known block kinds. */
export interface UnknownBlock {
  type: string;
  [key: string]: unknown;
}

export type Block =
  | MarkdownBlock
  | StatRowBlock
  | RoadmapBlock
  | CodeEditorBlock
  | FeedbackBlock
  | ActionsBlock
  | UnknownBlock;

export interface Page {
  title: string;
  subtitle?: string;
  blocks: Block[];
}

export interface PageResponse {
  session_id: string;
  page: Page;
}

export interface AskResponse {
  answer: string;
}

export interface ClarifyTurn {
  question: string;
  answer: string;
}

export interface ClarifyResponse {
  ready: boolean;
  question: string | null;
}

/** One line of the live activity feed shown while a turn runs (backend/app/progress.py). `detail`
 * names the thing being worked on, and `ok === false` marks a step the agent had to work around. */
export interface ProgressStep {
  text: string;
  kind: string;
  detail?: string;
  ok?: boolean | null;
  done?: boolean;
  error?: string;
}

export interface ProgressResponse {
  steps: ProgressStep[];
  done: boolean;
}

/** Who the browser is signed in as (GET /api/auth/me). The GitHub token is deliberately absent: it
 * never leaves the server (see backend/app/auth.py). */
export interface Me {
  login: string;
  name: string | null;
  avatar_url: string | null;
}

/** One entry of GET /api/learn: enough to resume a project without naming it by hand. */
export interface SessionSummary {
  id: string;
  objective: string;
  updated_at: string;
}

export interface SessionListResponse {
  sessions: SessionSummary[];
}

/** One entry of GET /api/learn/{id}/exercises: enough to list a project's exercises and open one,
 * without the starter code (that arrives with the exercise itself). */
export interface ExerciseSummary {
  exercise_id: string;
  title: string;
  file_name: string;
  language: string;
  open_action: string;
}

export interface ExerciseListResponse {
  exercises: ExerciseSummary[];
}
