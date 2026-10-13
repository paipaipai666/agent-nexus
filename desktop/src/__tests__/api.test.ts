import { describe, it, expect, vi, beforeEach } from 'vitest'
import { api } from '../services/api'

const mockFetch = vi.fn()
global.fetch = mockFetch

function mockJsonResponse(data: unknown, ok = true, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(data), { status: ok ? status : status === 200 ? 500 : status }))
}

describe('api service', () => {
  beforeEach(() => {
    mockFetch.mockReset()
  })

  describe('createSession', () => {
    it('sends POST to /api/session', async () => {
      mockFetch.mockReturnValue(mockJsonResponse({ session_id: 'abc' }))

      const result = await api.createSession()

      expect(result.session_id).toBe('abc')
      const [url, options] = mockFetch.mock.calls[0]
      expect(url).toContain('/api/session')
      expect(options.method).toBe('POST')
    })

    it('passes skill parameter', async () => {
      mockFetch.mockReturnValue(mockJsonResponse({ session_id: 'abc' }))

      await api.createSession('coding')

      const [, options] = mockFetch.mock.calls[0]
      const body = JSON.parse(options.body)
      expect(body.skill).toBe('coding')
    })
  })

  describe('getSessions', () => {
    it('sends GET to /api/sessions', async () => {
      const sessions = [{ session_id: 's1', skill: null }]
      mockFetch.mockReturnValue(mockJsonResponse({ sessions }))

      const result = await api.getSessions()

      expect(result.sessions).toHaveLength(1)
      const [url] = mockFetch.mock.calls[0]
      expect(url).toContain('/api/sessions')
    })
  })

  describe('getTodos', () => {
    it('sends GET to session todos endpoint', async () => {
      mockFetch.mockReturnValue(mockJsonResponse({ items: [], count: 0 }))

      await api.getTodos('s1')

      const [url] = mockFetch.mock.calls[0]
      expect(url).toContain('/api/session/s1/todos')
    })
  })

  describe('listDocuments', () => {
    it('sends GET to /api/kb/documents', async () => {
      mockFetch.mockReturnValue(mockJsonResponse({ documents: [], total_chunks: 0 }))

      await api.listDocuments()

      const [url] = mockFetch.mock.calls[0]
      expect(url).toContain('/api/kb/documents')
    })
  })

  describe('searchKnowledge', () => {
    it('sends POST with query and top_k', async () => {
      mockFetch.mockReturnValue(mockJsonResponse({ results: [] }))

      await api.searchKnowledge('test query', 3)

      const [url, options] = mockFetch.mock.calls[0]
      expect(url).toContain('/api/kb/search')
      const body = JSON.parse(options.body)
      expect(body.query).toBe('test query')
      expect(body.top_k).toBe(3)
    })

    it('defaults top_k to 5', async () => {
      mockFetch.mockReturnValue(mockJsonResponse({ results: [] }))

      await api.searchKnowledge('query')

      const [, options] = mockFetch.mock.calls[0]
      const body = JSON.parse(options.body)
      expect(body.top_k).toBe(5)
    })
  })

  describe('listSkills', () => {
    it('sends GET to /api/skills', async () => {
      mockFetch.mockReturnValue(mockJsonResponse({ skills: [], count: 0 }))

      await api.listSkills()

      const [url] = mockFetch.mock.calls[0]
      expect(url).toContain('/api/skills')
    })
  })

  describe('listMemories', () => {
    it('sends GET with limit parameter', async () => {
      mockFetch.mockReturnValue(mockJsonResponse({ memories: [], count: 0 }))

      await api.listMemories(10)

      const [url] = mockFetch.mock.calls[0]
      expect(url).toContain('/api/memory/long?limit=10')
    })

    it('defaults limit to 20', async () => {
      mockFetch.mockReturnValue(mockJsonResponse({ memories: [], count: 0 }))

      await api.listMemories()

      const [url] = mockFetch.mock.calls[0]
      expect(url).toContain('limit=20')
    })
  })

  describe('listShortMemories', () => {
    it('sends GET to /api/memory/short', async () => {
      mockFetch.mockReturnValue(mockJsonResponse({ messages: [], count: 0 }))

      await api.listShortMemories()

      const [url] = mockFetch.mock.calls[0]
      expect(url).toContain('/api/memory/short')
    })
  })

  describe('getConfig / updateConfig', () => {
    it('gets config via GET', async () => {
      mockFetch.mockReturnValue(mockJsonResponse({ llm_model_id: 'gpt-4' }))

      const result = await api.getConfig()

      expect(result.llm_model_id).toBe('gpt-4')
    })

    it('updates config via PUT', async () => {
      mockFetch.mockReturnValue(mockJsonResponse({ status: 'ok' }))

      await api.updateConfig('llm_model_id', 'gpt-4')

      const [url, options] = mockFetch.mock.calls[0]
      expect(url).toContain('/api/config')
      expect(options.method).toBe('PUT')
      const body = JSON.parse(options.body)
      expect(body.key).toBe('llm_model_id')
      expect(body.value).toBe('gpt-4')
    })
  })

  describe('getStats / getLogs', () => {
    it('gets stats with days parameter', async () => {
      mockFetch.mockReturnValue(mockJsonResponse({ total_calls: 10 }))

      await api.getStats(14)

      const [url] = mockFetch.mock.calls[0]
      expect(url).toContain('/api/stats?days=14')
    })

    it('gets logs with days parameter', async () => {
      mockFetch.mockReturnValue(mockJsonResponse({ traces: [] }))

      await api.getLogs(3)

      const [url] = mockFetch.mock.calls[0]
      expect(url).toContain('/api/logs?days=3')
    })
  })

  describe('error handling', () => {
    it('throws error with detail from response body', async () => {
      mockFetch.mockReturnValue(
        mockJsonResponse({ detail: 'Bad request' }, false, 400)
      )

      await expect(api.createSession()).rejects.toThrow('Bad request')
    })

    it('throws error with error.message when detail is missing', async () => {
      mockFetch.mockReturnValue(
        mockJsonResponse({ error: { message: 'Internal error' } }, false, 500)
      )

      await expect(api.getConfig()).rejects.toThrow('Internal error')
    })

    it('reports HTTP status when the server returns an unreadable body', async () => {
      mockFetch.mockReturnValue(
        Promise.resolve(new Response('invalid json', { status: 502 }))
      )

      await expect(api.getConfig()).rejects.toThrow('HTTP 502')
    })

    it('falls back to HTTP status code when no detail available', async () => {
      mockFetch.mockReturnValue(
        mockJsonResponse({}, false, 404)
      )

      await expect(api.getConfig()).rejects.toThrow('HTTP 404')
    })
  })

  describe('request headers', () => {
    it('sets Content-Type to application/json', async () => {
      mockFetch.mockReturnValue(mockJsonResponse({}))

      await api.getConfig()

      const [, options] = mockFetch.mock.calls[0]
      expect(new Headers(options.headers).get('Content-Type')).toBe('application/json')
    })
  })
})
