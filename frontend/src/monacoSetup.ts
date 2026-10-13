/** Point @monaco-editor/react at the npm-bundled monaco-editor and its workers, built by Vite,
 * instead of its default behaviour of fetching both from a CDN at runtime -- which fails offline
 * or behind a restrictive proxy (observed in this project's own sandbox). Imported once from
 * main.tsx before any <Editor> mounts. Uses the `new URL(..., import.meta.url)` worker pattern
 * (Vite's documented way to let Rollup discover and bundle a worker entry); each URL must be a
 * static literal for Rollup's static analysis to find it, so there is one per worker rather than
 * a shared helper function. */
import { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";

function newWorker(url: URL): Worker {
  return new Worker(url, { type: "module" });
}

self.MonacoEnvironment = {
  getWorker(_workerId: string, label: string) {
    switch (label) {
      case "json":
        return newWorker(new URL("monaco-editor/esm/vs/language/json/json.worker.js", import.meta.url));
      case "css":
      case "scss":
      case "less":
        return newWorker(new URL("monaco-editor/esm/vs/language/css/css.worker.js", import.meta.url));
      case "html":
      case "handlebars":
      case "razor":
        return newWorker(new URL("monaco-editor/esm/vs/language/html/html.worker.js", import.meta.url));
      case "typescript":
      case "javascript":
        return newWorker(new URL("monaco-editor/esm/vs/language/typescript/ts.worker.js", import.meta.url));
      default:
        return newWorker(new URL("monaco-editor/esm/vs/editor/editor.worker.js", import.meta.url));
    }
  },
};

loader.config({ monaco });
