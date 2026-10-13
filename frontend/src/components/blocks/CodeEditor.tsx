import Editor from "@monaco-editor/react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import type { CodeEditorBlock } from "../../types";

export function CodeEditor({
  block,
  onAction,
  submitting,
}: {
  block: CodeEditorBlock;
  onAction: (action: string, payload?: Record<string, unknown>) => void;
  submitting: boolean;
}) {
  const [code, setCode] = useState(block.starter_code);

  return (
    <div className="block block-code-editor">
      <div className="code-editor-instructions">
        <ReactMarkdown>{block.instructions}</ReactMarkdown>
      </div>
      <div className="code-editor-shell">
        <div className="code-editor-filename">{block.file_name}</div>
        <Editor
          height="420px"
          language={block.language}
          value={code}
          onChange={(value) => setCode(value ?? "")}
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
