import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

/** The agent writes GitHub-flavored markdown (tables especially, for things like "how we'll scale
 * down dbt" comparisons) -- plain react-markdown doesn't parse GFM tables/strikethrough/task lists
 * without this plugin, and falls back to showing the raw "| a | b |" text. Used everywhere a block
 * renders agent-written markdown, so no block forgets it.
 *
 * rehype-highlight is what colours fenced code blocks. Without it a fence like ```python parsed
 * correctly and even kept its `language-python` class, but nothing read that class, so the code was
 * rendered as flat monochrome text -- the markdown was never the problem. It tokenises with
 * highlight.js and emits `<span class="hljs-keyword">` and friends, which styles.css colours.
 *
 * `detect: false` on purpose: the agent always writes the language after the fence, and letting
 * highlight.js guess would mis-colour the short snippets it uses (a two-line diff, a shell command).
 * `ignoreMissing` additionally keeps a fence whose language the agent invented from throwing and
 * taking the whole page down with it -- it degrades to unstyled code instead. */
export function Md({ children }: { children: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[[rehypeHighlight, { detect: false, ignoreMissing: true }]]}
    >
      {children}
    </ReactMarkdown>
  );
}
