import { useCallback, useSyncExternalStore } from 'react'
import type { AttachmentRef } from './api'

export interface ChatDraft {
  text: string
  attachments: AttachmentRef[]
}

const storageKey = 'agentnexus.chat-drafts.v1'
const emptyDraft: ChatDraft = { text: '', attachments: [] }
const listeners = new Set<() => void>()

function isAttachment(value: unknown): value is AttachmentRef {
  return typeof value === 'object' && value !== null
    && 'path' in value && typeof value.path === 'string'
    && 'name' in value && typeof value.name === 'string'
    && 'size' in value && typeof value.size === 'number' && Number.isFinite(value.size) && value.size >= 0
    && 'mime' in value && typeof value.mime === 'string'
}

function loadDrafts(): Record<string, ChatDraft> {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(storageKey) ?? '{}')
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return {}
    const result: Record<string, ChatDraft> = {}
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value !== 'object' || value === null || !('text' in value) || typeof value.text !== 'string'
        || !('attachments' in value) || !Array.isArray(value.attachments)) continue
      result[key] = { text: value.text, attachments: value.attachments.filter(isAttachment) }
    }
    return result
  } catch { return {} }
}

let drafts = loadDrafts()

export function getChatDraft(key: string): ChatDraft {
  return drafts[key] ?? emptyDraft
}

export function saveChatDraft(key: string, draft: ChatDraft) {
  const next = { ...drafts }
  if (!draft.text && draft.attachments.length === 0) delete next[key]
  else next[key] = draft
  drafts = next
  try { localStorage.setItem(storageKey, JSON.stringify(next)) } catch { }
  listeners.forEach(listener => listener())
}

export function clearChatDraft(key: string) {
  saveChatDraft(key, emptyDraft)
}

export function useChatDraft(key: string) {
  const draft = useSyncExternalStore(
    listener => { listeners.add(listener); return () => { listeners.delete(listener) } },
    () => getChatDraft(key),
  )
  const setInput = useCallback((value: string | ((previous: string) => string)) => {
    const current = drafts[key] ?? emptyDraft
    saveChatDraft(key, { ...current, text: typeof value === 'function' ? value(current.text) : value })
  }, [key])
  const setAttachments = useCallback((value: AttachmentRef[] | ((previous: AttachmentRef[]) => AttachmentRef[])) => {
    const current = drafts[key] ?? emptyDraft
    saveChatDraft(key, { ...current, attachments: typeof value === 'function' ? value(current.attachments) : value })
  }, [key])
  return { input: draft.text, attachments: draft.attachments, setInput, setAttachments }
}
