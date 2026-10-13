import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** The agent writes GitHub-flavored markdown (tables especially, for things like "how we'll scale
 * down dbt" comparisons) -- plain react-markdown doesn't parse GFM tables/strikethrough/task lists
 * without this plugin, and falls back to showing the raw "| a | b |" text. Used everywhere a block
 * renders agent-written markdown, so no block forgets it. */
export function Md({ children }: { children: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>;
}
