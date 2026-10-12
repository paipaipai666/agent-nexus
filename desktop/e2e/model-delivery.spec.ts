import { expect } from '@playwright/test'
import { test } from './fixtures'
import type { KnowledgeModels } from '../src/services/api'

test('knowledge model download shows size, failure and retry in the running UI', async ({ page, mocks }, testInfo) => {
  void mocks
  let requests = 0
  let state: KnowledgeModels = {
    models: {
      embedding: { name: 'embedding', state: 'cached', message: 'Cached locally', identity: 'embedding@revision', download_bytes: 96_405_966 },
      reranker: { name: 'reranker', state: 'missing', message: 'Model has not been downloaded', download_bytes: 2_293_259_337 },
    }, index_state: 'ready',
  }
  await page.route('**/api/kb/documents', route => route.fulfill({ json: { documents: [], total_chunks: 0 } }))
  await page.route('**/api/kb/models', route => route.fulfill({ json: state }))
  await page.route('**/api/kb/models/reranker/prepare', route => {
    requests++
    state = {
      ...state,
      models: {
        ...state.models,
        reranker: requests === 1
          ? { name: 'reranker', state: 'failed', message: 'Connection interrupted; retry preparation', download_bytes: 2_293_259_337 }
          : { name: 'reranker', state: 'ready', message: 'Enhanced ranking is ready', identity: 'reranker@revision', download_bytes: 2_293_259_337 },
      },
    }
    return route.fulfill({ json: { model: state.models.reranker } })
  })
  await page.goto('/settings/knowledge')
  await expect(page.getByText('Enhanced ranking · 2.14 GiB')).toBeVisible()
  await expect(page.getByText('Search uses hybrid ranking until this optional model is ready.')).toBeVisible()
  expect(requests).toBe(0)
  await page.screenshot({ path: testInfo.outputPath('first-use.png'), fullPage: true })
  await page.getByRole('button', { name: 'Download model' }).click()
  await expect(page.getByText('Connection interrupted; retry preparation')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Retry preparation' })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('retry.png'), fullPage: true })
  await page.getByRole('button', { name: 'Retry preparation' }).click()
  await expect(page.getByText('Connection interrupted; retry preparation')).not.toBeVisible()
  await expect(page.getByRole('button', { name: 'Search', exact: true })).toBeEnabled()
  expect(requests).toBe(2)
})
