import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../services/api'

afterEach(() => vi.unstubAllGlobals())

describe('knowledge model status boundary', () => {
  it('rejects a ready model without a verified identity', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      models: {
        embedding: { name: 'embedding', state: 'ready', message: 'Ready', download_bytes: 1 },
        reranker: { name: 'reranker', state: 'missing', message: 'Missing', download_bytes: 2 },
      }, index_state: 'ready',
    }))))
    await expect(api.getKnowledgeModels()).rejects.toThrow('Missing model identity')
  })

  it('distinguishes a cached model from a loaded model', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      models: {
        embedding: { name: 'embedding', state: 'cached', message: 'Cached', download_bytes: 1, identity: 'model@revision' },
        reranker: { name: 'reranker', state: 'missing', message: 'Missing', download_bytes: 2 },
      }, index_state: 'rebuild_required',
    }))))
    const value = await api.getKnowledgeModels()
    expect(value.models.embedding.state).toBe('cached')
    expect(value.index_state).toBe('rebuild_required')
  })
})
