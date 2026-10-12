import { test, expect } from '@playwright/test'
import { installMockWebSocket, mockApi } from './fixtures'

test('shows the branded waiting page while application code and online fonts are delayed', async ({ page }) => {
  let releaseScript = () => {}
  let releaseFonts = () => {}
  const scriptGate = new Promise<void>(resolve => { releaseScript = resolve })
  const fontGate = new Promise<void>(resolve => { releaseFonts = resolve })
  await page.route('**/src/main.tsx', async route => {
    await scriptGate
    await route.continue()
  })
  await page.route('https://fonts.googleapis.com/**', async route => {
    await fontGate
    await route.abort()
  })
  try {
    await page.goto('/', { waitUntil: 'commit' })
    await expect(page.getByRole('heading', { name: 'AgentNexus', exact: true })).toBeVisible()
    await expect(page.getByRole('status')).toHaveText('Loading application...')
    const logo = page.getByRole('img', { name: 'AgentNexus logo' })
    await expect(logo).toBeVisible()
    await expect.poll(() => logo.evaluate(image => image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0)).toBe(true)
    await expect(page.locator('.startup-screen')).toHaveCSS('background-color', 'rgb(17, 19, 24)')
  } finally {
    releaseScript()
    releaseFonts()
  }
})

test('loads the application without waiting for online fonts and toggles its sidebar', async ({ page }) => {
  let releaseFonts = () => {}
  const fontGate = new Promise<void>(resolve => { releaseFonts = resolve })
  await page.route('https://fonts.googleapis.com/**', async route => {
    await fontGate
    await route.abort()
  })
  installMockWebSocket(page)
  await mockApi(page)
  try {
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    const hide = page.getByRole('button', { name: '隐藏侧栏', exact: true })
    await expect(hide).toBeVisible()
    await expect(page.locator('#navigation-sidebar')).toHaveAttribute('aria-hidden', 'false')
    await hide.click()
    await expect(page.locator('#navigation-sidebar')).toHaveAttribute('aria-hidden', 'true')
    await expect(page.getByRole('button', { name: '显示侧栏', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '新建聊天', exact: true })).toHaveCount(0)
    await page.keyboard.press('Control+b')
    await expect(page.getByRole('button', { name: '隐藏侧栏', exact: true })).toBeVisible()
    await expect(page.locator('#navigation-sidebar')).toHaveAttribute('aria-hidden', 'false')
    await page.screenshot({ path: '../.research_tmp/logo-startup-e2e/titlebar-sidebar-control.png' })
  } finally {
    releaseFonts()
  }
})

test('shows backend startup errors on the local waiting page', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'electronAPI', {
      value: {
        close: () => {},
        onBackendError: (callback: (message: string) => void) => {
          const listener = (event: Event) => {
            if (event instanceof CustomEvent && typeof event.detail === 'string') callback(event.detail)
          }
          window.addEventListener('test-backend-error', listener)
          return () => window.removeEventListener('test-backend-error', listener)
        },
      },
    })
  })
  await page.goto('/loading.html')
  await expect(page.getByRole('status')).toHaveText('Starting backend...')
  await expect(page.getByRole('button', { name: 'Close AgentNexus' })).toBeVisible()
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('test-backend-error', { detail: 'Backend is unavailable' })))
  await expect(page.getByRole('alert')).toHaveText('Backend is unavailable')
  await expect(page.getByRole('status')).toHaveText('Unable to start AgentNexus')
  await expect(page.locator('#spinner')).toBeHidden()
})
