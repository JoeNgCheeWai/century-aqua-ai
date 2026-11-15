import { Fragment } from "react";
import { parseMarkdown, type Block, type Inline } from "@/lib/mc-markdown";

/**
 * Shows an agent's reply with its headings, numbered steps, bold and code formatted, in the console's dark style. It renders from the
 * data lib/mc-markdown.ts produces, so text is always a React string (escaped): a "<script>" in a reply is shown as text and there is
 * no dangerouslySetInnerHTML anywhere. Plain text with no markdown looks like ordinary paragraphs, as before.
 */
function Pieces({ parts }: { parts: Inline[] }) {
  return (
    <>
      {parts.map((p, i) =>
        p.kind === "bold" ? (
          <strong key={i} className="font-semibold text-white">
            {p.text}
          </strong>
        ) : p.kind === "code" ? (
          <code key={i} className="rounded bg-slate-800 px-1 py-0.5 font-mono text-[0.85em] text-cyan-200">
            {p.text}
          </code>
        ) : (
          <Fragment key={i}>{p.text}</Fragment>
        ),
      )}
    </>
  );
}

function BlockView({ block }: { block: Block }) {
  switch (block.kind) {
    case "heading":
      return (
        <h4 className="mt-1 text-sm font-semibold tracking-wide text-cyan-200">
          <Pieces parts={block.text} />
        </h4>
      );
    case "ordered":
      return (
        <ol start={block.start} className="list-decimal space-y-1.5 pl-6 marker:font-semibold marker:text-cyan-300">
          {block.items.map((item, i) => (
            <li key={i}>
              <Pieces parts={item} />
            </li>
          ))}
        </ol>
      );
    case "bullets":
      return (
        <ul className="list-disc space-y-1.5 pl-6 marker:text-cyan-400/70">
          {block.items.map((item, i) => (
            <li key={i}>
              <Pieces parts={item} />
            </li>
          ))}
        </ul>
      );
    case "code":
      return <pre className="overflow-x-auto rounded-lg border border-slate-800 bg-black/40 p-3 font-mono text-xs text-slate-200">{block.text}</pre>;
    default:
      return (
        <p>
          {block.lines.map((line, i) => (
            <Fragment key={i}>
              {i > 0 && <br />}
              <Pieces parts={line} />
            </Fragment>
          ))}
        </p>
      );
  }
}

export default function MarkdownText({ text, className = "" }: { text: string; className?: string }) {
  return (
    <div className={`space-y-3 text-sm leading-relaxed text-slate-100 ${className}`}>
      {parseMarkdown(text).map((block, i) => (
        <BlockView key={i} block={block} />
      ))}
    </div>
  );
}
