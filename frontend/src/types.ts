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
}

export interface FeedbackBlock {
  type: "feedback";
  passed: boolean;
  summary: string;
  details: string;
}

export interface ActionItem {
  action: string;
  label: string;
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
