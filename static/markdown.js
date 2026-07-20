// Minimal Markdown renderer for chat bubbles.
//
// Why not just use marked.js/showdown from a CDN? Two reasons: (1) this
// project's whole pitch is "free and dependency-light", and pulling a
// third-party script from a CDN for bold/italic/code/lists is a lot of
// trust and a runtime dependency for very little payoff; (2) it lets the
// Content-Security-Policy in security.py stay at `script-src 'self'`
// with no exceptions.
//
// Everything the model can produce is HTML-escaped BEFORE any markdown
// syntax is interpreted, so there's no way for model output (which is
// untrusted, effectively user-influenced content) to inject markup.
// This is the one part of the frontend that has to be careful about
// XSS, since bot replies are rendered via innerHTML further down.

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderInline(text) {
  let out = escapeHtml(text);

  // Inline code: `code`
  out = out.replace(/`([^`\n]+)`/g, (_, code) => `<code>${code}</code>`);

  // Bold: **text** or __text__
  out = out.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");

  // Italic: *text* or _text_ (after bold, so ** isn't eaten by *)
  out = out.replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, "<em>$1</em>");
  out = out.replace(/(?<!_)_([^_\n]+)_(?!_)/g, "<em>$1</em>");

  // Links: [text](https://...) — scheme is restricted to http/https only.
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, url) => {
    return `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  });

  return out;
}

function renderMarkdown(rawText) {
  const text = String(rawText ?? "");
  const lines = text.split("\n");
  const htmlParts = [];

  let i = 0;
  let listBuffer = [];
  let listType = null; // "ul" | "ol"

  function flushList() {
    if (!listBuffer.length) return;
    const tag = listType === "ol" ? "ol" : "ul";
    htmlParts.push(`<${tag}>${listBuffer.map((item) => `<li>${renderInline(item)}</li>`).join("")}</${tag}>`);
    listBuffer = [];
    listType = null;
  }

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block
    const fenceMatch = line.match(/^```(\w*)\s*$/);
    if (fenceMatch) {
      flushList();
      const lang = fenceMatch[1] || "";
      const codeLines = [];
      i += 1;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        codeLines.push(lines[i]);
        i += 1;
      }
      i += 1; // skip closing fence
      const codeText = escapeHtml(codeLines.join("\n"));
      const langAttr = lang ? ` data-lang="${escapeHtml(lang)}"` : "";
      htmlParts.push(`<pre${langAttr}><code>${codeText}</code></pre>`);
      continue;
    }

    // Unordered list item
    const ulMatch = line.match(/^\s*[-*]\s+(.*)$/);
    if (ulMatch) {
      if (listType !== "ul") flushList();
      listType = "ul";
      listBuffer.push(ulMatch[1]);
      i += 1;
      continue;
    }

    // Ordered list item
    const olMatch = line.match(/^\s*\d+\.\s+(.*)$/);
    if (olMatch) {
      if (listType !== "ol") flushList();
      listType = "ol";
      listBuffer.push(olMatch[1]);
      i += 1;
      continue;
    }

    flushList();

    // Heading
    const headingMatch = line.match(/^(#{1,4})\s+(.*)$/);
    if (headingMatch) {
      const level = Math.min(headingMatch[1].length + 2, 6); // keep headings visually modest in a chat bubble
      htmlParts.push(`<h${level}>${renderInline(headingMatch[2])}</h${level}>`);
      i += 1;
      continue;
    }

    if (line.trim() === "") {
      i += 1;
      continue;
    }

    htmlParts.push(`<p>${renderInline(line)}</p>`);
    i += 1;
  }

  flushList();
  return htmlParts.join("");
}
