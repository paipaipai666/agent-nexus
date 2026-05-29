import { describe, it, expect, vi, beforeEach } from 'vitest'
import { api, setApiKey } from '../services/api'

const mockFetch = vi.fn()
global.fetch = mockFetch

function mockJsonResponse(data: any, ok = true, status = 200) {
  return Promise.resolve({
    ok,
    status,
    json: () => Promise.resolve(data),
    statusText: 'OK',
  } as Response)
}

describe('api service', () => {
  beforeEach(() => {
    mockFetch.mockReset()
    setApiKey(null)
  })

  describe('setApiKey', () => {
    it('sets API key header on subsequent requests', async () => {
      setApiKey('test-key-123')
      mockFetch.mockReturnValue(mockJsonResponse({ session_id: 's1' }))

      await api.createSession()

      const [, options] = mockFetch.mock.calls[0]
      expect(options.headers['X-API-Key']).toBe('test-key-123')
    })

    it('does not send API key header when key is null', async () => {
      setApiKey(null)
      mockFetch.mockReturnValue(mockJsonResponse({ session_id: 's1' }))

      await api.createSession()

      const [, options] = mockFetch.mock.calls[0]
      expect(options.headers['X-API-Key']).toBeUndefined()
    })
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

  describe('sendMessage', () => {
    it('sends POST with session_id and content', async () => {
      mockFetch.mockReturnValue(mockJsonResponse({ run_id: 'r1', answer: 'hi', status: 'done' }))

      const result = await api.sendMessage('s1', 'hello')

      expect(result.answer).toBe('hi')
      const [, options] = mockFetch.mock.calls[0]
      const body = JSON.parse(options.body)
      expect(body.session_id).toBe('s1')
      expect(body.content).toBe('hello')
    })
  })

  describe('cancelRun', () => {
    it('sends POST with run_id', async () => {
      mockFetch.mockReturnValue(mockJsonResponse({ status: 'cancelled' }))

      await api.cancelRun('run-123')

      const [, options] = mockFetch.mock.calls[0]
      const body = JSON.parse(options.body)
      expect(body.run_id).toBe('run-123')
    })
  })

  describe('confirmTool', () => {
    it('sends POST with approved=true', async () => {
      mockFetch.mockReturnValue(mockJsonResponse({ status: 'confirmed' }))

      await api.confirmTool('run-1', true)

      const [, options] = mockFetch.mock.calls[0]
      const body = JSON.parse(options.body)
      expect(body.approved).toBe(true)
    })

    it('sends POST with approved=false', async () => {
      mockFetch.mockReturnValue(mockJsonResponse({ status: 'rejected' }))

      await api.confirmTool('run-1', false)

      const [, options] = mockFetch.mock.calls[0]
      const body = JSON.parse(options.body)
      expect(body.approved).toBe(false)
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
        Promise.resolve({
          ok: false,
          status: 400,
          json: () => Promise.resolve({ detail: 'Bad request' }),
          statusText: 'Bad Request',
        } as Response)
      )

      await expect(api.createSession()).rejects.toThrow('Bad request')
    })

    it('throws error with error.message when detail is missing', async () => {
      mockFetch.mockReturnValue(
        Promise.resolve({
          ok: false,
          status: 500,
          json: () => Promise.resolve({ error: { message: 'Internal error' } }),
          statusText: 'Internal Server Error',
        } as Response)
      )

      await expect(api.getConfig()).rejects.toThrow('Internal error')
    })

    it('falls back to statusText when JSON parse fails', async () => {
      mockFetch.mockReturnValue(
        Promise.resolve({
          ok: false,
          status: 502,
          json: () => Promise.reject(new Error('parse error')),
          statusText: 'Bad Gateway',
        } as Response)
      )

      await expect(api.getConfig()).rejects.toThrow('Bad Gateway')
    })

    it('falls back to HTTP status code when no detail available', async () => {
      mockFetch.mockReturnValue(
        Promise.resolve({
          ok: false,
          status: 404,
          json: () => Promise.resolve({}),
          statusText: 'Not Found',
        } as Response)
      )

      await expect(api.getConfig()).rejects.toThrow('HTTP 404')
    })
  })

  describe('request headers', () => {
    it('sets Content-Type to application/json', async () => {
      mockFetch.mockReturnValue(mockJsonResponse({}))

      await api.getConfig()

      const [, options] = mockFetch.mock.calls[0]
      expect(options.headers['Content-Type']).toBe('application/json')
    })
  })
})
