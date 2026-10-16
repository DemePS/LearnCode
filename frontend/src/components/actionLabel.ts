import type { Page, RoadmapBlock } from "../../types";

/** Remembers the roadmap's exercises by the action that opens them.
 *
 * A feedback page has no roadmap and no code editor on it -- only the feedback and the buttons -- so it
 * cannot say by itself which exercise a "retry" button refers to. The roadmap is what knows, and it was
 * on screen a moment earlier, so it is kept here as the learner moves: one map from open_action to the
 * exercise, plus its position, which is how the agent already numbers them ("exercise 6").
 *
 * Rebuilt whenever a roadmap comes back, so it follows the project as exercises are added or renumbered. */
export interface ExerciseIndex {
  byAction: Record<string, { id: string; number: number; title: string }>;
}

export const EMPTY_INDEX: ExerciseIndex = { byAction: {} };

export function indexRoadmap(page: Page, previous: ExerciseIndex): ExerciseIndex {
  const roadmap = page.blocks.find((b): b is RoadmapBlock => b.type === "roadmap");
  if (!roadmap) return previous;
  const byAction: ExerciseIndex["byAction"] = {};
  roadmap.items.forEach((item, position) => {
    byAction[item.open_action] = { id: item.id, number: position + 1, title: item.title };
  });
  return { byAction };
}

/** The exercise an action refers to, or null.
 *
 * A retry button does not necessarily reuse the roadmap item's open_action, however firmly the prompt
 * asks it to: the button on the failing page sent "retry_ex_risk_gate" while the roadmap opened the
 * exercise with "open_ex_risk_gate". Matching the id outright would then miss, and the button would be
 * labelled from the raw id ("Retry ex risk gate"), which is worse than useless. So the exercise id is
 * looked for *inside* the action too -- "retry_<id>" and "open_<id>" name the same exercise. */
function exerciseFor(action: string, index: ExerciseIndex) {
  const direct = index.byAction[action];
  if (direct) return direct;
  const lower = action.toLowerCase();
  let best: ExerciseIndex["byAction"][string] | null = null;
  for (const [openAction, exercise] of Object.entries(index.byAction)) {
    // The roadmap's own open_action is authoritative when the id contains it.
    if (lower.includes(openAction.toLowerCase())) return exercise;
    // Otherwise the exercise's own id ("ex_risk_gate") is the link: prefer the longest match, so
    // "ex_1" cannot claim an action about "ex_10".
    const id = exercise.id.toLowerCase();
    if (id && lower.includes(id) && (!best || id.length > best.id.length)) best = exercise;
  }
  return best;
}

/** What a button should say, decided here rather than by the model.
 *
 * The agent invents an action id and gets it back verbatim; the wording on the button is interface text,
 * which is ours. Leaving it to the model is how a retry came back as "Retry exercise 6 with the worked
 * shape" -- promising the solution the learner is supposed to be finding, on the one button whose whole
 * purpose is to send them back to try again. The text also belongs to the app's voice, not the model's.
 *
 * Anything unrecognised still gets a readable label rather than nothing: a button with no text is worse
 * than a vague one, and the id usually names what will happen ("run_tests" -> "Run tests"). */
export function actionLabel(action: string, index: ExerciseIndex): string {
  const exercise = exerciseFor(action, index);
  if (exercise) return `Retry exercise ${exercise.number}`;

  if (action === "show_roadmap" || includes(action, "roadmap")) return "Back to the roadmap";
  if (includes(action, "next") || includes(action, "continue")) return "Next exercise";
  if (includes(action, "restart") || includes(action, "start over")) return "Start a new project";
  if (includes(action, "finish") || includes(action, "done") || includes(action, "complete")) {
    return "Finish";
  }
  // The id names what happens often enough to be the best description left. "submit_ex1" keeps its
  // leading verb because "Submit" is exactly the right word for a button that submits.
  return titleCase(action.replace(/[_-]+/g, " ").trim()) || "Continue";
}

/** Whole-word match, so "next" does not fire on "context" and "done" not on "abandoned". */
function includes(action: string, word: string): boolean {
  const words = action.toLowerCase().split(/[^a-z0-9]+/);
  if (words.includes(word)) return true;
  // Multi-word keys ("start over") arrive as one string, so compare the joined text too.
  return word.includes(" ") && action.toLowerCase().replace(/[_-]+/g, " ").includes(word);
}

function titleCase(text: string): string {
  if (!text) return "";
  return text[0].toUpperCase() + text.slice(1);
}
