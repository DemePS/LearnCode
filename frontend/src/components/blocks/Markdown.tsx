import type { MarkdownBlock } from "../../types";
import { Md } from "../Md";

export function Markdown({ block }: { block: MarkdownBlock }) {
  return (
    <div className="block block-markdown">
      <Md>{block.content}</Md>
    </div>
  );
}
