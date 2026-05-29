import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Sidebar from '../components/layout/Sidebar'

function renderSidebar(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Sidebar />
    </MemoryRouter>
  )
}

describe('Sidebar', () => {
  it('renders all navigation items', () => {
    renderSidebar()

    expect(screen.getByTitle('Chat')).toBeInTheDocument()
    expect(screen.getByTitle('Knowledge')).toBeInTheDocument()
    expect(screen.getByTitle('Skills')).toBeInTheDocument()
    expect(screen.getByTitle('Memory')).toBeInTheDocument()
    expect(screen.getByTitle('Settings')).toBeInTheDocument()
    expect(screen.getByTitle('Stats')).toBeInTheDocument()
  })

  it('highlights the active route', () => {
    renderSidebar('/')

    const chatButton = screen.getByTitle('Chat')
    expect(chatButton.className).toContain('text-accent-primary')
  })

  it('does not highlight inactive routes', () => {
    renderSidebar('/')

    const knowledgeButton = screen.getByTitle('Knowledge')
    expect(knowledgeButton.className).not.toContain('text-accent-primary')
  })

  it('highlights knowledge when on knowledge route', () => {
    renderSidebar('/knowledge')

    const knowledgeButton = screen.getByTitle('Knowledge')
    expect(knowledgeButton.className).toContain('text-accent-primary')

    const chatButton = screen.getByTitle('Chat')
    expect(chatButton.className).not.toContain('text-accent-primary')
  })

  it('renders as a nav element', () => {
    const { container } = renderSidebar()

    const nav = container.querySelector('nav')
    expect(nav).toBeInTheDocument()
  })
})
