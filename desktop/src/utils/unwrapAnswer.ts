export function unwrapStreamingAnswer(raw: string): string {
  if (!raw) return raw
  const trimmed = raw.trimStart()
  if (!trimmed.startsWith('{')) return raw

  try {
    const obj = JSON.parse(trimmed)
    if (obj && typeof obj === 'object' && typeof (obj as { answer?: unknown }).answer === 'string') {
      return (obj as { answer: string }).answer
    }
  } catch {
  }

  const m = trimmed.match(/^\{\s*"answer"\s*:\s*"/)
  if (!m) {
    if (/^\{\s*"ans/.test(trimmed)) return ''
    return raw
  }

  let body = trimmed.slice(m[0].length)
  body = body.replace(/"\s*\}\s*$/, '')
  return body
    .replace(/\\n/g, '\n')
    .replace(/\\r/g, '\r')
    .replace(/\\t/g, '\t')
    .replace(/\\"/g, '"')
    .replace(/\\\\/g, '\\')
}
