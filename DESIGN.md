# AgentNexus Design System

## Overview

AgentNexus is a local-first AI agent platform with a dark-themed desktop interface. The design system prioritizes readability, information density, and a clean developer-tool aesthetic.

**Design Read:** Electron desktop app for developers, dark-theme-first, Claude Desktop-inspired minimal layout, CSS variables for dual-mode theming.

## Register

**Product** — design serves the tool. The interface is a working environment, not a marketing surface.

## Color System

### Dark Theme (Default)

| Token | Value | Usage |
|-------|-------|-------|
| `--surface-0` | `#0a0e14` | Deepest background (body) |
| `--surface-1` | `#141921` | Sidebar, titlebar, statusbar |
| `--surface-2` | `#1c2333` | Cards, input backgrounds, main content |
| `--surface-3` | `#2d3a50` | Hover states, elevated elements, active nav |
| `--surface-4` | `#384660` | Pressed states, deep hover |
| `--fg` | `#e6edf3` | Primary text |
| `--fg-secondary` | `#8b949e` | Secondary text |
| `--fg-muted` | `#484f58` | Muted text (timestamps, labels) |
| `--fg-faint` | `#30363d` | Faint text (dividers, disabled) |
| `--accent` | `#58a6ff` | Primary accent (Electric Blue) |
| `--accent-hover` | `#79c0ff` | Accent hover state |
| `--accent-muted` | `rgba(88,166,255,0.15)` | Accent backgrounds |
| `--accent-subtle` | `rgba(88,166,255,0.06)` | Subtle accent tint |
| `--accent-glow` | `rgba(88,166,255,0.12)` | Glow effects |
| `--green` | `#3fb950` | Success, connected, positive |
| `--green-muted` | `rgba(63,185,80,0.15)` | Success backgrounds |
| `--red` | `#f85149` | Error, destructive, disconnected |
| `--red-muted` | `rgba(248,81,73,0.15)` | Error backgrounds |
| `--amber` | `#d29922` | Warning, running, pending |
| `--amber-muted` | `rgba(210,153,34,0.15)` | Warning backgrounds |
| `--blue` | `#58a6ff` | Info, links (same as accent) |
| `--purple` | `#bc8cff` | Facts, metadata |
| `--cyan` | `#39d3f5` | Secondary accent, data viz |
| `--border` | `rgba(255,255,255,0.12)` | Default borders |
| `--border-subtle` | `rgba(255,255,255,0.06)` | Subtle dividers |
| `--border-strong` | `rgba(255,255,255,0.22)` | Emphasized borders |
| `--border-active` | `rgba(88,166,255,0.35)` | Focus rings, active inputs |

### Light Theme

| Token | Value | Usage |
|-------|-------|-------|
| `--surface-0` | `#f6f8fa` | Body background |
| `--surface-1` | `#ffffff` | Sidebar, cards, inputs |
| `--surface-2` | `#f6f8fa` | Secondary surfaces |
| `--surface-3` | `#eaeef2` | Hover states |
| `--surface-4` | `#d0d7de` | Pressed states |
| `--fg` | `#1f2328` | Primary text |
| `--fg-secondary` | `#57606a` | Secondary text |
| `--fg-muted` | `#8c959f` | Muted text |
| `--fg-faint` | `#afb8c1` | Faint text |
| `--accent` | `#0969da` | Primary accent (deeper blue) |
| `--accent-hover` | `#0550ae` | Accent hover |
| `--border` | `rgba(27,31,36,0.12)` | Default borders |
| `--border-strong` | `rgba(27,31,36,0.20)` | Emphasized borders |

### Semantic Colors

| Role | Dark | Light | Usage |
|------|------|-------|-------|
| Success | `--green` | `--green` | Connected, done, positive |
| Error | `--red` | `--red` | Error, failed, destructive |
| Warning | `--amber` | `--amber` | Running, pending, queued |
| Info | `--blue` | `--blue` | Informational, links |

## Typography

### Font Stack

| Role | Font | Fallback |
|------|------|----------|
| Sans (UI) | DM Sans | -apple-system, BlinkMacSystemFont, sans-serif |
| Serif (prose) | Source Serif 4 | Georgia, serif |
| Mono (data) | Fira Code | Consolas, monospace |

### Type Scale

| Element | Size | Weight | Line Height |
|---------|------|--------|-------------|
| Base body | 14px | 400 | 1.5 |
| Markdown body | 15px | 400 | 1.7 |
| Headings (h1) | 1.25rem | 600 | 1.3 |
| Headings (h2) | 1.125rem | 600 | 1.3 |
| Headings (h3) | 1rem | 600 | 1.3 |
| Small labels | 11px | 500 | 1.4 |
| Mono code | 13px | 400 | 1.6 |

### Typography Rules

- Serif font (Source Serif 4) used only for markdown body content (agent responses)
- Sans font (DM Sans) used for all UI elements
- Mono font (Fira Code) used for data, logs, timestamps, session IDs
- `text-wrap: balance` on headings for even line lengths

## Spacing & Layout

### Spacing Scale

| Token | Value | Usage |
|-------|-------|-------|
| `--radius` | 6px | Default border radius |
| `--radius-lg` | 6px | Large radius (same as default) |
| `--radius-xl` | 6px | Extra large radius (same as default) |

### Layout Structure

```
┌─────────────────────────────────────────────────────────┐
│ Titlebar (h-10) — App identity + window controls        │
├──────────┬──────────────────────────────────────────────┤
│ Sidebar  │ Main Content Area                            │
│ (220px)  │                                              │
│          │  ┌─────────────────────────────────────────┐ │
│ - New    │  │ Messages / Page Content                 │ │
│   Chat   │  │                                         │ │
│ - Recent │  │                                         │ │
│   chats  │  │                                         │ │
│ - Nav    │  └─────────────────────────────────────────┘ │
│   items  │  ┌─────────────────────────────────────────┐ │
│ - System │  │ Input Area (floating)                   │ │
│   items  │  └─────────────────────────────────────────┘ │
├──────────┴──────────────────────────────────────────────┤
│ StatusBar (h-7) — Connection, model, context, tokens    │
└─────────────────────────────────────────────────────────┘
```

### Key Dimensions

- Sidebar: 220px width
- Titlebar: 40px height (h-10)
- StatusBar: 28px height (h-7)
- Max content width: 768px (max-w-3xl) for chat messages

## Shadows

| Token | Value | Usage |
|-------|-------|-------|
| `--shadow-sm` | `0 1px 2px rgba(0,0,0,0.15)` | Cards at rest |
| `--shadow-md` | `0 2px 8px rgba(0,0,0,0.2)` | Elevated elements |
| `--shadow-lg` | `0 4px 16px rgba(0,0,0,0.25)` | Modals, overlays |
| `--shadow-elevated` | `0 4px 16px rgba(0,0,0,0.25)` | Floating elements |

## Components

### Buttons

**Primary Button** (`btn-primary`)
- Background: `var(--accent)`
- Text: white
- Border-radius: 6px
- Padding: 7px 14px
- Hover: lighter accent, subtle glow

**Ghost Button** (`btn-ghost`)
- Background: transparent
- Text: `var(--fg-secondary)`
- Border: 1px solid `var(--border)`
- Hover: surface-2 background, brighter text

### Input Fields (`input-field`)

- Background: `var(--surface-1)`
- Border: 1px solid `var(--border)`
- Border-radius: 6px
- Padding: 7px 12px
- Focus: accent border, no glow

### Cards (`surface-card`)

- Background: `var(--surface-2)`
- Border: 1px solid `var(--border)`
- Border-radius: 6px

### Collapsible Sections

- Chevron icon (ChevronRight/ChevronDown) indicates expandable
- Header shows summary, expanded shows full content
- Used for: tool output, system messages

## Chat Interface

### Message Types

| Type | Style | Behavior |
|------|-------|----------|
| User | Accent "You" label, no background | Always visible |
| Assistant | Serif font, markdown rendered | Always visible |
| System | Mono font, muted color | Collapsed by default |
| Tool | Tool card with status indicator | Collapsed when done, expanded when running |

### Tool Card

- Header: tool name + status badge (running/done/error)
- Body: mono font output, diff syntax highlighting
- Collapsible: auto-expand when running, collapse when done

### Empty State

- Centered layout
- "How can I help you?" heading (28px)
- Description text (15px)
- Suggestion pills (no border, surface-2 background)

### Input Area

- Floating at bottom with padding
- Rounded corners (6px)
- Command palette appears above when typing `/`
- HUD row: checkpoint info + undo/redo/history buttons

## Sidebar Navigation

### Structure

1. **New Chat** button (top)
2. **Recent Sessions** (inline list, max 8)
3. **Divider**
4. **Nav Items**: Knowledge, Skills, MCP, Memory, Plugins
5. **Divider**
6. **System Items**: Settings, Stats, Health, Alerts, Audit, Eval

### Nav Item States

- Default: `var(--fg-muted)` text, transparent background
- Hover: `var(--fg-secondary)` text, `var(--surface-2)` background
- Active: `var(--fg)` text, `var(--surface-3)` background, accent icon

## Motion

- Transition duration: 150ms ease for hover states
- Button press: `transform: scale(0.98)`
- Collapsible: chevron rotation
- Messages: GSAP entrance animation (slide + fade)
- Respects `prefers-reduced-motion: reduce`

## Accessibility

- Focus ring: 2px solid accent, 2px offset
- Reduced motion: all animations disabled
- WCAG AA contrast minimum (targeting AAA where possible)
- Semantic HTML throughout
- Keyboard navigation supported

## Anti-Patterns (Avoid)

- No neon/outer glows
- No pure black (#000000)
- No gradient text
- No side-stripe borders >1px
- No em-dashes
- No decorative status dots
- No section-numbering eyebrows
- No card-heavy layouts where spacing would work
