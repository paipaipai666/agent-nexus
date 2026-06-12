# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: sidebar-session.spec.ts >> Sidebar session visibility >> shows session in sidebar immediately after first message
- Location: e2e\sidebar-session.spec.ts:4:7

# Error details

```
TimeoutError: page.waitForSelector: Timeout 15000ms exceeded.
Call log:
  - waiting for locator('textarea') to be visible

```

# Test source

```ts
  99  | 
  100 |   // Version status
  101 |   await page.route(backendContains('/api/version/status'), (route) =>
  102 |     route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ session_id: 'test', head: null, can_undo: false, can_redo: false }) })
  103 |   )
  104 | 
  105 |   // Version log
  106 |   await page.route(backendContains('/api/version/log'), (route) =>
  107 |     route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ checkpoints: [] }) })
  108 |   )
  109 | 
  110 |   // Generic API catch-all — must be LAST
  111 |   await page.route(backendContains('/api/'), (route) => {
  112 |     route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  113 |   })
  114 | 
  115 |   return {
  116 |     setRecentSessions(sessions: any[]) {
  117 |       recentSessions = sessions
  118 |     },
  119 |   }
  120 | }
  121 | 
  122 | /**
  123 |  * Inject a mock WebSocket that captures sent messages and allows
  124 |  * the test to simulate server events.
  125 |  */
  126 | export function installMockWebSocket(page: Page) {
  127 |   page.addInitScript(() => {
  128 |     // @ts-expect-error — override for testing
  129 |     window.__mockWsSent = []
  130 |     // @ts-expect-error — override for testing
  131 |     window.__mockWsHandler = null
  132 |     // @ts-expect-error — override for testing
  133 |     window.__mockWsInstance = null
  134 | 
  135 |     class MockWebSocket {
  136 |       static CONNECTING = 0
  137 |       static OPEN = 1
  138 |       static CLOSING = 2
  139 |       static CLOSED = 3
  140 | 
  141 |       readyState = 1
  142 |       onopen: ((ev: Event) => void) | null = null
  143 |       onclose: ((ev: CloseEvent) => void) | null = null
  144 |       onmessage: ((ev: MessageEvent) => void) | null = null
  145 |       onerror: ((ev: Event) => void) | null = null
  146 | 
  147 |       constructor(_url: string) {
  148 |         // @ts-expect-error — override for testing
  149 |         window.__mockWsInstance = this
  150 |         setTimeout(() => this.onopen?.(new Event('open')), 10)
  151 |       }
  152 |       send(data: string) {
  153 |         const parsed = JSON.parse(data)
  154 |         // @ts-expect-error — override for testing
  155 |         window.__mockWsSent.push(parsed)
  156 |       }
  157 |       close() { this.readyState = 3 }
  158 |       addEventListener(type: string, handler: any) {
  159 |         if (type === 'message') {
  160 |           // @ts-expect-error — override for testing
  161 |           window.__mockWsHandler = handler
  162 |         }
  163 |       }
  164 |       removeEventListener() {}
  165 |     }
  166 | 
  167 |     // @ts-expect-error — override
  168 |     window.WebSocket = MockWebSocket
  169 |   })
  170 | 
  171 |   return {
  172 |     getSentMessages: () => page.evaluate(() => (window as any).__mockWsSent),
  173 |     emit: (eventType: string, data: any) => {
  174 |       page.evaluate(({ type, payload }) => {
  175 |         const ws = (window as any).__mockWsInstance
  176 |         if (ws?.onmessage) {
  177 |           ws.onmessage(new MessageEvent('message', { data: JSON.stringify({ type, ...payload }) }))
  178 |         }
  179 |       }, { type: eventType, payload: data })
  180 |     },
  181 |     clearSent: () => page.evaluate(() => { (window as any).__mockWsSent = [] }),
  182 |   }
  183 | }
  184 | 
  185 | type AgentNexusFixtures = {
  186 |   mocks: {
  187 |     api: Awaited<ReturnType<typeof mockApi>>
  188 |     ws: ReturnType<typeof installMockWebSocket>
  189 |   }
  190 | }
  191 | 
  192 | export const test = base.extend<AgentNexusFixtures>({
  193 |   mocks: async ({ page }, use) => {
  194 |     const ws = installMockWebSocket(page)
  195 |     const api = await mockApi(page)
  196 |     await page.goto('/')
  197 |     // Wait for app to get past the loading screen
  198 |     try {
> 199 |       await page.waitForSelector('textarea', { timeout: 15_000 })
      |                  ^ TimeoutError: page.waitForSelector: Timeout 15000ms exceeded.
  200 |     } catch (err) {
  201 |       await page.screenshot({ path: 'test-results/debug.png' })
  202 |       // Log page errors
  203 |       const html = await page.content()
  204 |       const bodyMatch = html.match(/<body[^>]*>([\s\S]*?)<\/body>/)
  205 |       console.log('\n=== BODY HTML ===')
  206 |       console.log(bodyMatch?.[1]?.slice(0, 2000) || 'NO BODY FOUND')
  207 |       console.log('=== END ===\n')
  208 |       throw err
  209 |     }
  210 |     await use({ api, ws })
  211 |   },
  212 | })
  213 | 
  214 | export { expect } from '@playwright/test'
  215 | 
```