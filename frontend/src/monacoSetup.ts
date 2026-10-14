/** Point @monaco-editor/react at the npm-bundled monaco-editor and its workers, built by Vite,
 * instead of its default behaviour of fetching both from a CDN at runtime.
 *
 * Imported once from main.tsx before any <Editor> mounts, and uses the `new URL(..., import.meta.url)`
 * worker pattern (Vite's documented way to let Rollup discover and bundle a worker entry). Each URL
 * must be a static literal for Rollup's static analysis to find it, so there is one per worker rather
 * than a shared helper function.
 *
 * The specifiers deliberately have NO `esm/vs/` prefix. monaco-editor's package.json `exports` map
 * already expands them:
 *
 *     "./*"     -> "./esm/vs/*.js"
 *     "./*.js"  -> "./esm/vs/*.js"
 *
 * so "monaco-editor/esm/vs/editor/editor.worker.js" resolves to esm/vs/esm/vs/editor/... -- a path
 * that does not exist. Vite then served its SPA fallback (index.html) for the worker request, and the
 * browser refused it with 'non-JavaScript MIME type of "text/html"'. Monaco's response to a worker
 * that will not start is to fall back to the main thread, which silently produced an editor with no
 * syntax colours -- the visible symptom that led here. Write these paths relative to esm/vs/, never
 * from the package root.
 */
import { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";

function newWorker(url: URL): Worker {
  return new Worker(url, { type: "module" });
}

self.MonacoEnvironment = {
  getWorker(_workerId: string, label: string) {
    switch (label) {
      case "json":
        return newWorker(new URL("monaco-editor/language/json/json.worker.js", import.meta.url));
      case "css":
      case "scss":
      case "less":
        return newWorker(new URL("monaco-editor/language/css/css.worker.js", import.meta.url));
      case "html":
      case "handlebars":
      case "razor":
        return newWorker(new URL("monaco-editor/language/html/html.worker.js", import.meta.url));
      case "typescript":
      case "javascript":
        return newWorker(new URL("monaco-editor/language/typescript/ts.worker.js", import.meta.url));
      default:
        return newWorker(new URL("monaco-editor/editor/editor.worker.js", import.meta.url));
    }
  },
};

loader.config({ monaco });
