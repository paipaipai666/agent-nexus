import { test, expect } from './fixtures'

test.use({ reducedMotion: 'reduce' })
test.beforeEach(async ({ page }) => {
  await page.route('**/fonts.googleapis.com/**', route => route.abort())
  await page.route('**/fonts.gstatic.com/**', route => route.abort())
})

test('communication receipts, retry, cancellation and narrow layout', async ({ page, mocks }, testInfo) => {
  mocks.api.setRecentSessions([{ session_id: 's', question: 'Improve collaboration', updated_at: 1 }])
  await page.route('**/api/sessions/recent*', route => route.fulfill({ json: { sessions: [{ session_id: 's', preview: '优化代理协作', workspace_path: 'D:/code/AgentNexus', last_message_at: '2026-10-14T04:00:00', updated_at: '2026-10-14T04:00:00', created_at: '2026-10-14T04:00:00', profile: null }], count: 1 } }))
  const started = Date.now() / 1000 - 74
  const first = { subagent_id: 'a', name: '接口核对', task: '核对通信接口与取消行为，汇总待确认的问题。', role: 'explorer', status: 'waiting', current_tool: '', started_at: started, finished_at: null, steps_used: 4, error: '', child_run_id: 'attempt_a', updated_at: started + 60, collaborative: true }
  const second = { ...first, subagent_id: 'b', name: '回归验证', task: '运行相关回归，检查多会话隔离。', status: 'thinking', child_run_id: 'attempt_b', role: 'executor' }
  const question = { id: 1, session_id: 's', run_id: 'p', request_id: 'q', subagent_id: 'a', child_run_id: 'attempt_a', sender: 'a', recipient: 'parent', kind: 'question', content: '历史消息是否保留完整正文？', question_id: '', delivery: 'applied', step_id: 3, question_state: 'open', reason: '', revision: 2, ts: started + 60 }
  let messages = [question]
  const submissions: Array<{ request_id: string; content: string; question_id?: number }> = []
  let rejectSend = true
  let cancelled = false
  await page.route('**/api/session/s/subagents', route => route.fulfill({ json: { session_id: 's', collaboration_enabled: true, subagents: [{ ...first, ...(cancelled ? { status: 'cancelling', updated_at: Date.now() / 1000 } : {}) }, second] } }))
  await page.route('**/api/session/s/events*', route => route.fulfill({ json: { session_id: 's', events: [], last_id: 0, count: 0 } }))
  await page.route('**/api/session/s/subagents/*/messages*', async route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: { messages: route.request().url().includes('/a/') ? messages : [], count: route.request().url().includes('/a/') ? messages.length : 0, last_id: messages.at(-1)?.id ?? 0 } })
    const input = route.request().postDataJSON()
    submissions.push(input)
    if (rejectSend) {
      rejectSend = false
      return route.fulfill({ status: 503, json: { error: { message: '存储暂时不可用，请重试' } } })
    }
    const receipt = { ...question, id: 2, request_id: input.request_id, sender: 'user', recipient: 'a', content: input.content, kind: 'reply', question_id: input.question_id, question_state: '', delivery: 'queued', revision: 1 }
    messages = [{ ...question, question_state: 'answered', revision: 3 }, receipt]
    return route.fulfill({ json: receipt })
  })
  await page.route('**/api/session/s/subagents/a/cancel', route => {
    cancelled = true
    return route.fulfill({ json: { accepted: true, subagent_id: 'a', subagent: { ...first, status: 'cancelling', updated_at: Date.now() / 1000 } } })
  })

  await page.goto('/chat/s/subagent/a')
  await expect(page.getByRole('heading', { name: '协作工作区' })).toBeVisible()
  await expect(page.getByText('新会话', { exact: true })).toHaveCount(0)
  await expect(page.getByText('待答问题 · 1')).toBeVisible()
  await page.getByRole('button', { name: '补充答复' }).click()
  await page.getByRole('textbox', { name: '补充要求或答复' }).fill('保留完整正文，并记录回执。')
  const contrast = (element: Element) => {
    const channels = (value: string) => {
      const values = value.match(/[\d.]+/g)?.map(Number)
      if (!values || values.length < 3) throw new Error('Unsupported computed color')
      return values
    }
    const style = getComputedStyle(element)
    const background = channels(style.backgroundColor)
    const foreground = channels(getComputedStyle(element, '::placeholder').color)
    const alpha = foreground[3] ?? 1
    const luminance = (rgb: number[]) => rgb.slice(0, 3).map(value => value / 255).map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4).reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0)
    const ink = luminance(foreground.slice(0, 3).map((value, index) => value * alpha + background[index] * (1 - alpha)))
    const paper = luminance(background)
    return (Math.max(ink, paper) + .05) / (Math.min(ink, paper) + .05)
  }
  expect(await page.getByRole('textbox', { name: '补充要求或答复' }).evaluate(contrast)).toBeGreaterThanOrEqual(4.5)
  await page.getByRole('button', { name: '发送', exact: true }).click()
  await expect(page.getByText(/存储暂时不可用/)).toBeVisible()
  await expect(page.getByRole('textbox', { name: '补充要求或答复' })).toHaveValue('保留完整正文，并记录回执。')
  await page.getByRole('button', { name: '发送', exact: true }).click()
  await expect(page.getByText('已排队 · 答复 #1')).toBeVisible()
  expect(submissions[0].request_id).toBe(submissions[1].request_id)
  expect(submissions[1].question_id).toBe(1)
  await expect(page.getByText('待答问题 · 1')).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('collaboration-wide.png') })
  await page.getByRole('button', { name: '切换浅色主题' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await expect(page.locator('html')).not.toHaveClass(/theme-anim/)
  await expect(page.getByRole('textbox', { name: '补充要求或答复' })).toHaveCSS('background-color', 'rgb(255, 255, 255)')
  expect(await page.getByRole('textbox', { name: '补充要求或答复' }).evaluate(contrast)).toBeGreaterThanOrEqual(4.5)
  await page.screenshot({ path: testInfo.outputPath('collaboration-light.png') })
  await page.getByRole('button', { name: '中断', exact: true }).click()
  await expect(page.getByRole('button', { name: '取消中', exact: true })).toBeDisabled()
  await page.setViewportSize({ width: 700, height: 900 })
  await expect(page.getByRole('heading', { name: '接口核对' })).toBeVisible()
  await page.getByRole('button', { name: '代理列表', exact: true }).click()
  await page.getByRole('link', { name: /回归验证/ }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: '回归验证' })).toBeVisible()
  await expect(page.getByRole('textbox', { name: '补充要求或答复' })).toHaveValue('')
  await expect(page.getByText('历史消息是否保留完整正文？')).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('collaboration-narrow.png') })
})

test('late history cannot replace newer live communication', async ({ page, mocks }) => {
  await page.route('**/api/sessions/recent*', route => route.fulfill({ json: { sessions: [], count: 0 } }))
  const message = { id: 1, session_id: 's', run_id: 'p', request_id: 'i', subagent_id: 'a', child_run_id: 'c', sender: 'parent', recipient: 'a', kind: 'instruction', content: '检查超时清理', question_id: '', delivery: 'queued', step_id: 0, question_state: '', reason: '', revision: 1, ts: 1 }
  let releaseHistory: () => void = () => {}
  const delayed = new Promise<void>(resolve => { releaseHistory = resolve })
  await page.route('**/api/session/s/subagents', route => route.fulfill({ json: { session_id: 's', collaboration_enabled: true, subagents: [{ subagent_id: 'a', name: 'Reader', role: 'explorer', task: 'Read', status: 'thinking', current_tool: '', started_at: 1, finished_at: null, steps_used: 1, error: '', child_run_id: 'c' }] } }))
  await page.route('**/api/session/s/events*', route => route.fulfill({ json: { events: [], last_id: 0, count: 0 } }))
  await page.route('**/api/session/s/subagents/a/messages*', async route => { await delayed; await route.fulfill({ json: { messages: [message], last_id: 1, count: 1 } }) })
  await page.goto('/chat/s/subagent/a')
  await expect(page.getByRole('heading', { name: 'Reader' })).toBeVisible()
  await mocks.ws.emit('subagent_event', { subagent_id: 'a', run_id: 'p', seq: 5, kind: 'communication', messages: [{ ...message, delivery: 'applied', revision: 2, step_id: 2 }] })
  await expect(page.getByText('已进入上下文')).toBeVisible()
  releaseHistory()
  await expect(page.getByText('检查超时清理')).toHaveCount(1)
  await expect(page.getByText('已排队', { exact: true })).toHaveCount(0)
})
