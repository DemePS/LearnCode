import ReactMarkdown from "react-markdown";
import type { MarkdownBlock } from "../../types";

export function Markdown({ block }: { block: MarkdownBlock }) {
  return (
    <div className="block block-markdown">
      <ReactMarkdown>{block.content}</ReactMarkdown>
    </div>
  );
}
