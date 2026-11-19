// Pure helpers for the canvas (highlight-and-redraft) on the final page. No DOM access here, so
// frontend/devtools/canvas_helpers_test.mjs can run them under node; app.js does the DOM glue.

// Trims whitespace off both ends of a selection [start, end) inside `text` and returns
// {start, end, quote}, or null when nothing but whitespace is selected. `quote` always comes from
// the target's own text, so it matches what the server compares against.
export function trimSelection(text, start, end) {
  let s = Math.max(0, Math.min(start, text.length));
  let e = Math.max(s, Math.min(end, text.length));
  while (s < e && /\s/.test(text[s])) s++;
  while (e > s && /\s/.test(text[e - 1])) e--;
  if (s >= e) return null;
  return { start: s, end: e, quote: text.slice(s, e) };
}

// Placeholder the server puts in editable text for a locked link: "⟦L0⟧".
export const placeholderFor = (id) => `⟦${id}⟧`;
const PLACEHOLDER_RE = /⟦(L\d+)⟧/g;

// Splits server source text into [{type: "text", value} | {type: "chip", id, words, url}] parts.
// A placeholder with no matching link entry stays plain text rather than vanishing.
export function parseSource(text, links) {
  const byId = new Map((links || []).map((l) => [l.id, l]));
  const parts = [];
  let last = 0;
  for (const m of text.matchAll(PLACEHOLDER_RE)) {
    const link = byId.get(m[1]);
    if (!link) continue;
    if (m.index > last) parts.push({ type: "text", value: text.slice(last, m.index) });
    parts.push({ type: "chip", id: link.id, words: link.words, url: link.url });
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push({ type: "text", value: text.slice(last) });
  return parts;
}

// Turns parts back into the text /canvas/apply expects: chips become their placeholders again.
export function partsToText(parts) {
  return parts.map((p) => (p.type === "chip" ? placeholderFor(p.id) : p.value)).join("");
}

// Link ids a save would lose: present in the original links, absent from the edited parts, and
// not removed on purpose (the editor's × on a CMI chip). Also ids that now appear more than once.
export function linkProblems(links, parts, removedIds) {
  const seen = new Map();
  for (const p of parts) if (p.type === "chip") seen.set(p.id, (seen.get(p.id) || 0) + 1);
  const removed = new Set(removedIds || []);
  const lost = (links || []).filter((l) => !seen.has(l.id) && !removed.has(l.id)).map((l) => l.id);
  const doubled = [...seen].filter(([, n]) => n > 1).map(([id]) => id);
  return { lost, doubled };
}

// Words in a string, for the "N → M words" line.
export function wordCount(text) {
  return (String(text).match(/\S+/g) || []).length;
}

export const segmentsText = (segs) => (segs || []).map((s) => s.text).join("");

// Word-level difference between two texts, for the suggestion view: [{op: "same"|"del"|"ins", text}], in reading order.
// Tokens are words with their trailing spaces, compared by longest common subsequence (a paragraph is a few hundred
// tokens at most, so the table stays small).
export function wordDiff(before, after) {
  const tok = (s) => (s || "").match(/\S+\s*|\s+/g) || [];
  const a = tok(before), b = tok(after);
  const key = (t) => t.trim();
  const n = a.length, m = b.length;
  const L = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      L[i][j] = key(a[i]) === key(b[j]) ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    }
  }
  const out = [];
  const push = (op, text) => {
    const last = out[out.length - 1];
    if (last && last.op === op) last.text += text; else out.push({ op, text });
  };
  let i = 0, j = 0;
  while (i < n && j < m) {
    // the new text's last word has no space after it; the old one's keeps it apart from the deleted words that follow
    if (key(a[i]) === key(b[j])) { push("same", /\s$/.test(b[j]) ? b[j] : a[i]); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) push("del", a[i++]);
    else push("ins", b[j++]);
  }
  while (i < n) push("del", a[i++]);
  while (j < m) push("ins", b[j++]);
  return out;
}
