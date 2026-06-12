import { test as base, type Page } from '@playwright/test'

const BACKEND = '127.0.0.1:18765'

function isBackend(url: string | URL) {
  const href = typeof url === 'string' ? url : url.href
  return href.includes(BACKEND)
}

function backendContains(path: string) {
  return (url: string | URL) => isBackend(url) && (typeof url === 'string' ? url : url.href).includes(path)
}

/**
 * Set up API route mocks for the AgentNexus backend.
 * Call this BEFORE navigating to the page.
 */
export async function mockApi(page: Page) {
  // Health check — always OK
  await page.route(backendContains('/health'), (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"ok"}' })
  )

  // Create session
  let sessionCounter = 0
  await page.route(backendContains('/api/session'), (route) => {
    const url = route.request().url()
    if (route.request().method() === 'POST' && url.endsWith('/api/session')) {
      const id = `session_test_${++sessionCounter}`
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ session_id: id }) })
    } else {
      route.fallback()
    }
  })

  // Restore session
  await page.route(backendContains('/api/session/restore'), (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ session_id: 'restored', restored: true }) })
  )

  // Clear STM
  await page.route(backendContains('/api/memory/short/clear'), (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  )

  // Recent sessions — starts empty, can be updated per test
  let recentSessions: any[] = []
  await page.route(backendContains('/api/sessions/recent'), (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ sessions: recentSessions, count: recentSessions.length }),
    })
  )

  // Session history
  await page.route(backendContains('/api/memory/short/history'), (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [], count: 0 }) })
  )

  // STM list (must come AFTER /short/clear and /short/history to avoid conflicts)
  await page.route(backendContains('/api/memory/short'), (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [] }) })
  )

  // Runtime status
  await page.route(backendContains('/api/runtime/status'), (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ model_id: 'test/model', stm_tokens: 100, ctx_max: 10000, total_usage: { input_tokens: 50, output_tokens: 50 }, step_count: 1 }),
    })
  )

  // Config
  await page.route(backendContains('/api/config'), (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ cwd: '/tmp' }) })
  )

  // MCP tools
  await page.route(backendContains('/api/mcp/tools'), (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ tools: [] }) })
  )

  // Skills
  await page.route(backendContains('/api/skills'), (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ skills: [] }) })
  )

  // Extensions
  await page.route(backendContains('/api/extensions'), (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) })
  )

  // Todos
  await page.route(backendContains('/api/todos'), (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [], count: 0 }) })
  )

  // Version status
  await page.route(backendContains('/api/version/status'), (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ session_id: 'test', head: null, can_undo: false, can_redo: false }) })
  )

  // Version log
  await page.route(backendContains('/api/version/log'), (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ checkpoints: [] }) })
  )

  // Generic API catch-all — must be LAST
  await page.route(backendContains('/api/'), (route) => {
    route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })

  return {
    setRecentSessions(sessions: any[]) {
      recentSessions = sessions
    },
  }
}

/**
 * Inject a mock WebSocket that captures sent messages and allows
 * the test to simulate server events.
 */
export function installMockWebSocket(page: Page) {
  page.addInitScript(() => {
    // @ts-expect-error — override for testing
    window.__mockWsSent = []
    // @ts-expect-error — override for testing
    window.__mockWsHandler = null
    // @ts-expect-error — override for testing
    window.__mockWsInstance = null

    class MockWebSocket {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSING = 2
      static CLOSED = 3

      readyState = 1
      onopen: ((ev: Event) => void) | null = null
      onclose: ((ev: CloseEvent) => void) | null = null
      onmessage: ((ev: MessageEvent) => void) | null = null
      onerror: ((ev: Event) => void) | null = null
      private _listeners: Record<string, Function[]> = {}

      constructor(_url: string) {
        // @ts-expect-error — override for testing
        window.__mockWsInstance = this
        setTimeout(() => {
          this.onopen?.(new Event('open'))
          for (const fn of this._listeners['open'] || []) fn(new Event('open'))
        }, 10)
      }
      send(data: string) {
        const parsed = JSON.parse(data)
        // @ts-expect-error — override for testing
        window.__mockWsSent.push(parsed)
      }
      close() { this.readyState = 3 }
      addEventListener(type: string, handler: any) {
        if (!this._listeners[type]) this._listeners[type] = []
        this._listeners[type].push(handler)
        if (type === 'message') {
          // @ts-expect-error — override for testing
          window.__mockWsHandler = handler
        }
      }
      removeEventListener() {}
    }

    // @ts-expect-error — override
    window.WebSocket = MockWebSocket
  })

  return {
    getSentMessages: () => page.evaluate(() => (window as any).__mockWsSent),
    emit: (eventType: string, data: any) => {
      page.evaluate(({ type, payload }) => {
        const ws = (window as any).__mockWsInstance
        if (ws?.onmessage) {
          ws.onmessage(new MessageEvent('message', { data: JSON.stringify({ type, ...payload }) }))
        }
      }, { type: eventType, payload: data })
    },
    clearSent: () => page.evaluate(() => { (window as any).__mockWsSent = [] }),
  }
}

type AgentNexusFixtures = {
  mocks: {
    api: Awaited<ReturnType<typeof mockApi>>
    ws: ReturnType<typeof installMockWebSocket>
  }
}

export const test = base.extend<AgentNexusFixtures>({
  mocks: async ({ page }, use) => {
    const ws = installMockWebSocket(page)
    const api = await mockApi(page)
    // Capture page errors
    const errors: string[] = []
    page.on('pageerror', (err) => errors.push(err.message))
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text())
    })
    await page.goto('/', { waitUntil: 'networkidle' })
    // Wait for app to get past the loading screen
    try {
      await page.waitForSelector('textarea', { timeout: 15_000 })
    } catch (err) {
      await page.screenshot({ path: 'test-results/debug.png' })
      // Log page errors
      const html = await page.content()
      const bodyMatch = html.match(/<body[^>]*>([\s\S]*?)<\/body>/)
      console.log('\n=== BODY HTML ===')
      console.log(bodyMatch?.[1]?.slice(0, 2000) || 'NO BODY FOUND')
      console.log('=== END ===\n')
      throw err
    }
    await use({ api, ws })
  },
})

export { expect } from '@playwright/test'
