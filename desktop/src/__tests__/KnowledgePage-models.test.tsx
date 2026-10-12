import type { PropsWithChildren } from 'react'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { KnowledgeModels } from '../services/api'

vi.mock('../services/api', () => ({ api: {
  listDocuments: vi.fn(), getKnowledgeModels: vi.fn(), prepareKnowledgeModel: vi.fn(),
  uploadDocumentWithProgress: vi.fn(), getIngestionProgress: vi.fn(), searchKnowledge: vi.fn(),
} }))
vi.mock('border-beam', () => ({ BorderBeam: ({ children }: PropsWithChildren) => <div>{children}</div> }))
vi.mock('../components/theme/ThemeProvider', () => ({ useTheme: () => ({ theme: 'dark' }) }))
vi.mock('../utils/effects', () => ({ usePrefersReducedMotion: () => true }))
vi.mock('../utils/animations', () => ({ animateEntrance: vi.fn() }))

import { api } from '../services/api'
import KnowledgePage from '../pages/KnowledgePage'

const missing: KnowledgeModels = {
  models: {
    embedding: { name: 'embedding', state: 'missing', message: 'Model has not been downloaded', download_bytes: 96_405_966 },
    reranker: { name: 'reranker', state: 'missing', message: 'Model has not been downloaded', download_bytes: 2_293_259_337 },
  },
  index_state: 'empty',
}

async function flush() {
  await act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve() })
}

function upload(container: HTMLElement) {
  const input = container.querySelector('input[type="file"]')
  if (!(input instanceof HTMLInputElement)) throw new Error('Upload input is missing')
  fireEvent.change(input, { target: { files: [new File(['Knowledge'], 'fixture.md', { type: 'text/markdown' })] } })
}

describe('Knowledge model preparation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    vi.mocked(api.listDocuments).mockResolvedValue({ documents: [], total_chunks: 0 })
    vi.mocked(api.getKnowledgeModels).mockResolvedValue(missing)
    vi.mocked(api.prepareKnowledgeModel).mockResolvedValue(undefined)
  })
  afterEach(() => { cleanup(); vi.useRealTimers() })

  it('shows the download sizes and starts the optional model only after a click', async () => {
    render(<KnowledgePage />)
    await flush()
    expect(screen.getByText('Embedding model · 92 MiB')).toBeInTheDocument()
    expect(screen.getByText('Enhanced ranking · 2.14 GiB')).toBeInTheDocument()
    expect(api.prepareKnowledgeModel).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Download model' }))
    await flush()
    expect(api.prepareKnowledgeModel).toHaveBeenCalledExactlyOnceWith('reranker')
  })

  it('keeps a long model download active and preserves the eventual failure message', async () => {
    vi.useFakeTimers()
    vi.mocked(api.uploadDocumentWithProgress).mockResolvedValue({ status: 'processing', run_id: 'run1', filename: 'fixture.md' })
    const run = {
      run_id: 'run1', status: 'running', source_uri: 'fixture.md', documents_seen: 0, chunks_written: 0,
      error_message: '', metadata: { progress_stage: 'preparing_embedding', progress_pct: 10, progress_message: 'Downloading embedding' },
      started_at: null, finished_at: null,
    }
    vi.mocked(api.getIngestionProgress).mockResolvedValue(run)
    const { container } = render(<KnowledgePage />)
    await flush()
    upload(container)
    await flush()
    await act(async () => { await vi.advanceTimersByTimeAsync(45_000) })
    expect(screen.getByRole('button', { name: 'Processing...' })).toBeDisabled()
    expect(screen.getByText('Downloading embedding')).toBeInTheDocument()
    expect(screen.queryByText(/Timed out/)).not.toBeInTheDocument()
    vi.mocked(api.getIngestionProgress).mockResolvedValue({ ...run, status: 'failed', error_message: 'Network interrupted; retry preparation' })
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000) })
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(screen.getByText('Network interrupted; retry preparation')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Upload' })).toBeEnabled()
  })

  it('shows model errors from search instead of presenting an empty result', async () => {
    vi.mocked(api.getKnowledgeModels).mockResolvedValue({
      ...missing,
      models: { ...missing.models, embedding: { ...missing.models.embedding, state: 'cached', identity: 'model@revision' } },
      index_state: 'ready',
    })
    vi.mocked(api.searchKnowledge).mockRejectedValue(new Error('Embedding model cannot load; retry preparation'))
    render(<KnowledgePage />)
    await flush()
    fireEvent.change(screen.getByPlaceholderText('Search knowledge base...'), { target: { value: 'Knowledge' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    await flush()
    expect(screen.getByRole('alert')).toHaveTextContent('Embedding model cannot load; retry preparation')
  })
})
