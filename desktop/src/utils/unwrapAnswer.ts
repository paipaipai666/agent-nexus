/**
 * Streaming JSON-answer shell stripper.
 *
 * JSON protocol streams `{"answer": "..."}` as raw tokens. Users used to stare
 * at the escaped shell until the final parse. This unwraps progressively:
 * - complete JSON → inner answer string
 * - partial `{"answer": "…` → decoded body without the closing quote/brace
 * - anything else → unchanged
 */
export function unwrapStreamingAnswer(raw: string): string {
  if (!raw) return raw
  const trimmed = raw.trimStart()
  if (!trimmed.startsWith('{')) return raw

  // Fast path: fully parseable answer envelope
  try {
    const obj = JSON.parse(trimmed)
    if (obj && typeof obj === 'object' && typeof (obj as { answer?: unknown }).answer === 'string') {
      return (obj as { answer: string }).answer
    }
  } catch {
    /* incomplete stream — fall through */
  }

  const m = trimmed.match(/^\{\s*"answer"\s*:\s*"/)
  if (!m) {
    // Still typing the key/prefix — hide the shell
    if (/^\{\s*"ans/.test(trimmed)) return ''
    return raw
  }

  let body = trimmed.slice(m[0].length)
  // Drop a completed trailing `"}` if present
  body = body.replace(/"\s*\}\s*$/, '')
  // Unescape common JSON string sequences (order matters)
  return body
    .replace(/\\n/g, '\n')
    .replace(/\\r/g, '\r')
    .replace(/\\t/g, '\t')
    .replace(/\\"/g, '"')
    .replace(/\\\\/g, '\\')
}
