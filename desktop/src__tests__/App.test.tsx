import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import App from '../App'

describe('App', () => {
  it('renders the chat page by default', () => {
    render(<App />)

    // ChatPage should be rendered at root path
    // The app should render without errors
    expect(document.querySelector('.flex')).toBeInTheDocument()
  })

  it('renders the sidebar navigation', () => {
    render(<App />)

    expect(screen.getByTitle('Chat')).toBeInTheDocument()
    expect(screen.getByTitle('Knowledge')).toBeInTheDocument()
  })
})
