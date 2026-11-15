/**
 * A small, SAFE markdown reader for the Mission Control chat. It turns an agent's reply into plain data (blocks and inline pieces);
 * components/mc/MarkdownText.tsx turns that data into React elements. Nothing here builds HTML.
 *
 * WHAT IT UNDERSTANDS (exactly what the CTO's communication protocol asks for, and nothing more):
 *   ### Heading        (also # and ##; always shown as a small section heading)
 *   1. numbered steps  (also "1)"), - or * bullets, an indented line continues the item above it
 *   **bold**           and   `code`   inside any line,   and   ```fenced code```   blocks
 * EVERYTHING ELSE IS PLAIN TEXT, shown exactly as written: no links, no images, no HTML, no tables, no italics. A reply with no markdown
 * comes out as ordinary paragraphs, so the other executives' plain replies look as they always did.
 *
 * WHY THIS IS SAFE. The output is data: text is only ever a string that React then escapes. A "<script>" or an "[x](javascript:...)" in a
 * reply stays visible text, because this reader never produces a tag or a link. An unclosed ** or ` stays literal. Inside a code
 * block or an inline code span nothing is interpreted. There is no dependency to keep patched.
 *
 * Pure functions, no React, no imports: plain node can test them (scripts/mission_control_markdown.test.mjs).
 */

export type Inline = { kind: "text" | "bold" | "code"; text: string };

export type Block =
  | { kind: "heading"; text: Inline[] }
  | { kind: "paragraph"; lines: Inline[][] }
  | { kind: "ordered"; start: number; items: Inline[][] }
  | { kind: "bullets"; items: Inline[][] }
  | { kind: "code"; text: string };

/** Longest reply the reader will process, so a runaway reply cannot freeze the page. Longer text is cut and says so. */
export const MAX_MARKDOWN_CHARS = 20000;
const CUT_NOTE = "\n\n(The rest of this reply is not shown here. Open the full recommendation below.)";

/** **bold** and `code` inside one line. An unclosed marker is left as literal text. */
export function parseInline(line: string): Inline[] {
  const out: Inline[] = [];
  let text = "";
  const flush = () => {
    if (text) out.push({ kind: "text", text });
    text = "";
  };
  let i = 0;
  while (i < line.length) {
    if (line.startsWith("**", i)) {
      const end = line.indexOf("**", i + 2);
      if (end > i + 2) {
        flush();
        out.push({ kind: "bold", text: line.slice(i + 2, end) });
        i = end + 2;
        continue;
      }
    }
    if (line[i] === "`") {
      const end = line.indexOf("`", i + 1);
      if (end > i + 1) {
        flush();
        out.push({ kind: "code", text: line.slice(i + 1, end) });
        i = end + 1;
        continue;
      }
    }
    text += line[i];
    i += 1;
  }
  flush();
  return out;
}

const HEADING = /^\s{0,3}#{1,4}\s+(\S.*?)\s*#*\s*$/;
const ORDERED = /^\s{0,3}(\d{1,3})[.)]\s+(\S.*)$/;
const BULLET = /^\s{0,3}[-*]\s+(\S.*)$/;
const FENCE = /^\s{0,3}```/;
const CONTINUATION = /^\s{2,}\S/;

/** Split a reply into blocks. Never throws, and never returns nothing for a non-empty reply. */
export function parseMarkdown(source: string): Block[] {
  let text = (source ?? "").replace(/\r\n?/g, "\n");
  if (text.length > MAX_MARKDOWN_CHARS) text = text.slice(0, MAX_MARKDOWN_CHARS) + CUT_NOTE;
  const lines = text.split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];

  const closeParagraph = () => {
    if (paragraph.length) blocks.push({ kind: "paragraph", lines: paragraph.map(parseInline) });
    paragraph = [];
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];

    if (FENCE.test(line)) {
      closeParagraph();
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !FENCE.test(lines[i])) {
        body.push(lines[i]);
        i += 1;
      }
      blocks.push({ kind: "code", text: body.join("\n") }); // an unclosed fence runs to the end: shown as code, never lost
      continue;
    }

    if (!line.trim()) {
      closeParagraph();
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      closeParagraph();
      blocks.push({ kind: "heading", text: parseInline(heading[1]) });
      continue;
    }

    const ordered = ORDERED.exec(line);
    const bullet = ordered ? null : BULLET.exec(line);
    if (ordered || bullet) {
      closeParagraph();
      const items: string[] = [(ordered ?? bullet)![ordered ? 2 : 1]];
      const start = ordered ? Number(ordered[1]) : 1;
      const same = ordered ? ORDERED : BULLET;
      while (i + 1 < lines.length) {
        const next = lines[i + 1];
        const more = same.exec(next);
        if (more) {
          items.push(more[ordered ? 2 : 1]);
        } else if (CONTINUATION.test(next) && !ORDERED.test(next) && !BULLET.test(next)) {
          items[items.length - 1] += " " + next.trim(); // an indented line belongs to the item above it
        } else {
          break;
        }
        i += 1;
      }
      const parsed = items.map(parseInline);
      blocks.push(ordered ? { kind: "ordered", start, items: parsed } : { kind: "bullets", items: parsed });
      continue;
    }

    paragraph.push(line);
  }
  closeParagraph();
  return blocks;
}

/** True if the text uses anything this reader would format. Lets a caller keep the old plain look for plain text. */
export function hasMarkdown(source: string): boolean {
  return parseMarkdown(source).some((b) => b.kind !== "paragraph" || b.lines.some((l) => l.some((p) => p.kind !== "text")));
}
