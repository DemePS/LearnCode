import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
// Must run before any <Editor> mounts: it points @monaco-editor/react at the bundled monaco-editor
// and its workers instead of fetching both from a CDN at runtime, and registers the language
// definitions that colour the code. CodeEditor.tsx imports it too, but that only happens when its
// lazy chunk loads -- which is after the editor is asked to render.
import "./monacoSetup";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
