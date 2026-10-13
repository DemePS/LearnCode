import Editor from "@monaco-editor/react";
import { useEffect, useState } from "react";
import type { CodeEditorBlock } from "../../types";
import { Md } from "../Md";
import "../../monacoSetup"; // only loaded once this chunk is (see PageRenderer's React.lazy import of this file)

function CodeEditor({
  block,
  onAction,
  submitting,
  onCodeChange,
}: {
  block: CodeEditorBlock;
  onAction: (action: string, payload?: Record<string, unknown>) => void;
  submitting: boolean;
  onCodeChange: (exerciseId: string, fileName: string, code: string) => void;
}) {
  const [code, setCode] = useState(block.starter_code);

  // Report the starting code once on mount too, not just on edits, so the help widget has
  // something to go on even before the learner's first keystroke.
  useEffect(() => {
    onCodeChange(block.exercise_id, block.file_name, block.starter_code);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [block.exercise_id]);

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
      </div>
    </div>
  );
}

export default CodeEditor; // React.lazy (PageRenderer) needs a default export
