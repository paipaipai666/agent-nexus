import { readFile } from 'node:fs/promises'
import { test, expect, type Page } from '@playwright/test'
import { installMockWebSocket } from './fixtures'

const sessionId = 'recovery-browser'
const outcome = {
  run_id: 'recovery-run', session_id: sessionId, question: '保留这次请求', status: 'finished',
  answer: '已生成需要保留的完整结果', reason: '', detail: '', journal: ['tool done: file_write -> saved'],
  persisted: false, save_error: 'disk full', execution_active: false, started: true,
}

async function start(page: Page) {
  installMockWebSocket(page)
  await page.route(url => url.hostname === '127.0.0.1' && url.port === '18765', async route => {
    const path = new URL(route.request().url()).pathname
    let body: unknown = {}
    if (path === '/health') body = { status: 'ok' }
    else if (path === '/api/config') body = { cwd: 'C:/project', persona: { agent_name: 'Nexus', projects: [] } }
    else if (path === '/api/session/restore') body = { session_id: sessionId, restored: true }
    else if (path === `/api/session/${sessionId}`) body = { session_id: sessionId, workspace: 'C:/project', plan_mode: false, thinking_effort: null }
    else if (path.endsWith('/recovery')) body = { recovery: null }
    else if (path.includes('/submissions/')) { await route.fulfill({ status: 404, json: { error: { code: 'submission_not_found', message: '尚未收到此提交' } } }); return }
    else if (path.includes('/memory/short')) body = { messages: [], count: 0 }
    else if (path.endsWith('/todos')) body = { items: [], count: 0 }
    else if (path.endsWith('/subagents')) body = { subagents: [] }
    else if (path === '/api/skills') body = { skills: [] }
    else if (path === '/api/mcp/status') body = { servers: [] }
    else if (path === '/api/mcp/tools') body = { tools: [] }
    else if (path === '/api/runtime/status') body = { model_id: 'test/model', ctx_max: 10000, stm_tokens: 0, step_count: 0, total_usage: { input_tokens: 0, output_tokens: 0 }, context_usage: null, compaction: null }
    else if (path.endsWith('/llm/providers')) body = { providers: [], active_model: 'test/model', judge_model: '' }
    else if (path.includes('/version/status')) body = { session_id: sessionId, head: null, can_undo: false, can_redo: false }
    else if (path.includes('/version/log')) body = { checkpoints: [] }
    else if (path === '/api/sessions/recent') body = { sessions: [], count: 0 }
    await route.fulfill({ json: body })
  })
  await page.goto(`/chat/${sessionId}`)
  await expect(page.getByRole('textbox', { name: '消息输入' })).toBeVisible()
  await expect(page.getByText('已连接', { exact: true })).toBeVisible()
}

async function emit(page: Page, payload: Record<string, unknown>) {
  await page.evaluate(data => {
    Reflect.get(window, '__mockWsInstance').onmessage(new MessageEvent('message', { data: JSON.stringify(data) }))
  }, payload)
}

async function submissions(page: Page) {
  return page.evaluate(() => {
    const messages: unknown = Reflect.get(window, '__mockWsSent')
    if (!Array.isArray(messages)) return []
    return messages.filter(message => typeof message === 'object' && message !== null && message.type === 'send_message')
  })
}

test('恢复失败保留原会话与草稿，重试不会创建新会话', async ({ page }) => {
  await start(page)
  await page.getByRole('textbox', { name: '消息输入' }).fill('原会话中的草稿')
  let fails = true
  await page.route('**/api/session/restore', route => route.fulfill(fails ? { status: 503, json: { detail: '恢复服务离线' } } : { json: { session_id: sessionId, restored: true } }))
  await page.reload()
  await expect(page.getByText(/恢复会话失败/)).toBeVisible()
  await expect(page.getByRole('textbox', { name: '消息输入' })).toHaveValue('原会话中的草稿')
  fails = false
  await page.getByRole('button', { name: '重试恢复会话' }).click()
  await expect(page.getByText(/恢复会话失败/)).not.toBeVisible()
  await expect(page).toHaveURL(new RegExp(`/chat/${sessionId}$`))
})

test('回执超时保留原请求，重试使用相同提交 ID', async ({ page }) => {
  await page.clock.install()
  await start(page)
  await page.getByRole('textbox', { name: '消息输入' }).fill('保留这次请求')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.getByText('正在等待发送回执…')).toBeVisible()
  await page.clock.fastForward(16000)
  await expect(page.getByText(/等待发送回执超时/)).toBeVisible()
  await page.getByRole('button', { name: '重试原提交' }).click()
  const sent = await submissions(page)
  expect(sent.map(message => message.content)).toEqual(['保留这次请求', '保留这次请求'])
  expect(sent[1].client_message_id).toBe(sent[0].client_message_id)
})

test('未保存结果可导出并单独重试保存，刷新后仍有恢复记录', async ({ page }, info) => {
  await start(page)
  await page.getByRole('textbox', { name: '消息输入' }).fill(outcome.question)
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await emit(page, { type: 'run_started', run_id: outcome.run_id, seq: 0 })
  await emit(page, { ...outcome, type: 'answer', content: outcome.answer, seq: 1 })
  await emit(page, { type: 'save_failed', run_id: outcome.run_id, persisted: false, save_error: 'disk full', seq: 2 })
  await expect(page.getByText('任务已完成，结果尚未保存')).toBeVisible()
  const downloaded = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出恢复记录' }).click()
  const file = info.outputPath('recovery.json')
  await (await downloaded).saveAs(file)
  expect(JSON.parse(await readFile(file, 'utf8'))).toMatchObject({ answer: '已生成需要保留的完整结果', persisted: false, save_error: 'disk full' })
  await page.reload()
  await expect(page.getByText('任务已完成，结果尚未保存')).toBeVisible()
  await page.screenshot({ path: info.outputPath('unsaved-result.png'), fullPage: true })
  await page.route('**/api/chat/recovery-run/retry-save', route => route.fulfill({ json: { ...outcome, persisted: true, save_error: '' } }))
  await page.getByRole('button', { name: '重试保存结果' }).click()
  await expect(page.getByText('任务已完成，结果尚未保存')).not.toBeVisible()
  expect(await submissions(page)).toEqual([])
})

test('审批等待回执，过期审批不能再执行', async ({ page }) => {
  await start(page)
  await emit(page, { type: 'run_started', run_id: 'approval-run', seq: 0 })
  await emit(page, { type: 'confirm_request', run_id: 'approval-run', confirmation_id: 'approval-one', summary: '写入工作区文件', expires_at: Date.now() + 300000 })
  await page.getByRole('button', { name: '批准执行' }).click()
  await expect(page.getByText('正在等待确认回执…')).toBeVisible()
  await expect(page.getByRole('button', { name: '批准执行' })).toBeDisabled()
  await emit(page, { type: 'confirm_result', run_id: 'approval-run', confirmation_id: 'approval-one', status: 'accepted' })
  await expect(page.getByRole('button', { name: '批准执行' })).not.toBeVisible()
  await emit(page, { type: 'confirm_request', run_id: 'approval-run', confirmation_id: 'approval-two', summary: '第二次写入', expires_at: Date.now() + 300000 })
  await emit(page, { type: 'confirm_timeout', run_id: 'approval-run', confirmation_id: 'approval-two' })
  await expect(page.getByRole('button', { name: '批准执行' })).toBeDisabled()
  await expect(page.getByText('确认已过期或已处理，请查询任务状态。')).toBeVisible()
})

test('重连查询恢复完整答案并说明连接状态', async ({ page }) => {
  await start(page)
  await emit(page, { type: 'run_started', run_id: outcome.run_id, seq: 0 })
  await emit(page, { type: 'token', run_id: outcome.run_id, content: '半截答案', seq: 1 })
  await page.route(`**/api/session/${sessionId}/recovery`, route => route.fulfill({ json: { recovery: { ...outcome, persisted: true, save_error: '' } } }))
  await emit(page, { type: 'connection_error', message: '连接中断，请查询状态' })
  await expect(page.getByText('未连接', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '查询任务状态' }).click()
  await expect(page.getByText(outcome.answer, { exact: true })).toBeVisible()
  await expect(page.getByText('半截答案', { exact: true })).not.toBeVisible()
})

test('待办读取失败保留旧数据，并可独立重试', async ({ page }) => {
  await page.clock.install()
  await page.setViewportSize({ width: 1440, height: 900 })
  let fails = false
  await start(page)
  await page.route('**/api/session/recovery-browser/todos', route => route.fulfill(fails ? { status: 503, json: { detail: '待办服务离线' } } : { json: { items: [{ id: 1, description: '已读取的待办', status: 'pending' }], count: 1 } }))
  await page.clock.fastForward(11000)
  await expect(page.getByText('已读取的待办')).toBeVisible()
  fails = true
  await page.clock.fastForward(11000)
  await expect(page.getByText(/待办列表加载失败/)).toBeVisible()
  await expect(page.getByText('已读取的待办')).toBeVisible()
  fails = false
  await page.getByRole('button', { name: '重试待办列表' }).click()
  await expect(page.getByText(/待办列表加载失败/)).not.toBeVisible()
})

test('后端运行中断开后恢复连接，当前草稿仍在', async ({ page }) => {
  await page.clock.install()
  await start(page)
  await page.getByRole('textbox', { name: '消息输入' }).fill('不要丢失这份草稿')
  let online = false
  await page.route('**/health', route => route.fulfill({ status: online ? 200 : 503, json: {} }))
  await page.clock.fastForward(6000)
  await expect(page.getByText(/当前页面和草稿已保留/)).toBeVisible()
  online = true
  await page.getByRole('button', { name: '重试连接' }).click()
  await expect(page.getByText(/当前页面和草稿已保留/)).not.toBeVisible()
  await expect(page.getByRole('textbox', { name: '消息输入' })).toHaveValue('不要丢失这份草稿')
})
