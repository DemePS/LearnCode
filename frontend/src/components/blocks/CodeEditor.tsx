import Editor from "@monaco-editor/react";
import { useEffect, useState } from "react";
import type { CodeEditorBlock } from "../../types";
import { Md } from "../Md";
import "../../monacoSetup"; // only loaded once this chunk is (see PageRenderer's React.lazy import of this file)

function CodeEditor({
  block,
  draft,
  onAction,
  submitting,
  onCodeChange,
}: {
  block: CodeEditorBlock;
  /** The learner's saved code, restored when they come back to an exercise they already worked on. */
  draft?: string | null;
  onAction: (action: string, payload?: Record<string, unknown>) => void;
  submitting: boolean;
  onCodeChange: (exerciseId: string, fileName: string, code: string) => void;
}) {
  const initial = draft ?? block.starter_code;
  const [code, setCode] = useState(initial);

  // Report the starting code once on mount too, not just on edits, so the help widget has
  // something to go on even before the learner's first keystroke.
  useEffect(() => {
    onCodeChange(block.exercise_id, block.file_name, initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [block.exercise_id]);

  // The draft arrives with the page, and the same editor can stay mounted across a page change (React
  // reuses it by position), in which case useState would keep the code from the previous page -- showing
  // an empty editor over a draft the learner had already saved. Syncing on the draft itself covers both
  // the remount and the in-place refresh. A draft of undefined (nothing saved yet) leaves whatever is
  // typed alone: that is the "start from the starter code" case, not an instruction to clear the editor.
  useEffect(() => {
    if (draft != null) {
      setCode(draft);
      onCodeChange(block.exercise_id, block.file_name, draft);
    }
  }, [draft]);

  function update(value: string) {
    setCode(value);
    onCodeChange(block.exercise_id, block.file_name, value);
  }

  return (
    <div className="block block-code-editor">
      <div className="code-editor-instructions">
        <Md>{block.instructions}</Md>
      </div>
      <div className="code-editor-shell">
        <div className="code-editor-filename">{block.file_name}</div>
        <Editor
          height="420px"
          language={block.language}
          value={code}
          onChange={(value) => update(value ?? "")}
          theme="vs-dark"
          options={{ minimap: { enabled: false }, fontSize: 14 }}
        />
      </div>
      <div className="code-editor-actions">
        <button
          className="btn btn-primary"
          disabled={submitting}
          onClick={() =>
            onAction(block.submit_action, {
              exercise_id: block.exercise_id,
              file_name: block.file_name,
              code,
            })
          }
        >
          {submitting ? "Submitting…" : "Submit"}
        </button>
        {/* The only way back to the starter skeleton once the learner has typed: their draft is
            restored automatically (that is the point of it), so without this there is no way to ask
            for the exercise's own code again. */}
        <button
          className="btn btn-secondary"
          disabled={submitting || code === block.starter_code}
          onClick={() => update(block.starter_code)}
        >
          Reset to starter code
        </button>
      </div>
    </div>
  );
}

export default CodeEditor; // React.lazy (PageRenderer) needs a default export
