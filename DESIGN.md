# AgentNexus Design System

## Overview

AgentNexus is a local-first AI agent platform with a bold, Brutalist-inspired desktop interface. The design system prioritizes readability, bold typography, and a clean developer-tool aesthetic with primary color accents.

**Design Read:** Electron desktop app for developers, light-theme-first, Brutalist-inspired layout with hard shadows and bold typography, CSS variables for theming.

## Register

**Product** — design serves the tool. The interface is a working environment, not a marketing surface.

## Color System

### Light Theme (Default)

| Token | Value | Usage |
|-------|-------|-------|
| `--surface-0` | `#f0f0f0` | Body background |
| `--surface-1` | `#ffffff` | Sidebar, titlebar, cards |
| `--surface-2` | `#f5f5f5` | Secondary surfaces, tool tags |
| `--surface-3` | `#e5e5e5` | Hover states |
| `--surface-4` | `#d4d4d4` | Pressed states |
| `--fg` | `#0a0a0a` | Primary text |
| `--fg-secondary` | `#404040` | Secondary text |
| `--fg-muted` | `#737373` | Muted text (timestamps, labels) |
| `--fg-faint` | `#a3a3a3` | Faint text (dividers, disabled) |
| `--border` | `#e5e5e5` | Default borders |
| `--border-strong` | `#0a0a0a` | Emphasized borders (input boxes) |

### Primary Colors

| Token | Value | Usage |
|-------|-------|-------|
| `--accent` | `#e11d48` | Primary accent (Rose Red) |
| `--accent-hover` | `#be123c` | Accent hover state |
| `--accent-muted` | `#e11d4814` | Accent backgrounds |
| `--pure-red` | `#ef4444` | Status: error, disconnected |
| `--pure-yellow` | `#eab308` | Status: warning, degraded |
| `--pure-blue` | `#3b82f6` | Status: info, primary actions |
| `--pure-green` | `#22c55e` | Status: success, connected |

### Semantic Colors

| Role | Color | Usage |
|------|-------|-------|
| Success | `#22c55e` | Connected, done, positive |
| Error | `#ef4444` | Error, failed, destructive |
| Warning | `#eab308` | Running, pending, degraded |
| Info | `#3b82f6` | Informational, links |
| Purple | `#8b5cf6` | Metadata, facts |
| Cyan | `#06b6d4` | Secondary accent, data viz |

## Typography

### Font Stack

| Role | Font | Usage |
|------|------|-------|
| Display | Anton | Page titles, section headers (bold, all-caps) |
| Sans (UI) | Inter | Body text, labels, descriptions |
| Mono (data) | Geist Mono | Code, logs, timestamps, status badges |
| Mono (code) | JetBrains Mono | Code blocks, diffs |

### Type Scale

| Element | Size | Weight | Line Height |
|---------|------|--------|-------------|
| Display title | 42px | 700 | 1.2 |
| Page title | 22-26px | 700 | 1.3 |
| Section title | 15px | 600 | 1.3 |
| Body text | 14px | 400 | 1.5 |
| Markdown body | 15px | 400 | 1.7 |
| Small labels | 12px | 500 | 1.4 |
| Tiny labels | 10-11px | 500 | 1.4 |
| Mono code | 11-13px | 400 | 1.6 |

### Typography Rules

- Anton font used for page titles and section headers (all-caps, bold)
- Inter font used for all UI body text
- Geist Mono used for data, logs, timestamps, status badges
- JetBrains Mono used for code blocks and diff output
- Section labels use letter-spacing: 1.5-2px for emphasis

## Spacing & Layout

### Spacing Scale

| Token | Value | Usage |
|-------|-------|-------|
| `--radius` | 6px | Default border radius (cards, buttons) |
| `--radius-lg` | 8px | Sidebar left corners |

### Layout Structure

```
┌─────────────────────────────────────────────────────────┐
│ Titlebar (h-40) — App identity + window controls         │
├──────────┬──────────────────────────────────────────────┤
│ Sidebar  │ Main Content Area                            │
│ (220px)  │                                              │
│ rounded  │  ┌─────────────────────────────────────────┐ │
│ left     │  │ Messages / Page Content                 │ │
│ corners  │  │                                         │ │
│          │  │                                         │ │
│          │  └─────────────────────────────────────────┘ │
│          │  ┌─────────────────────────────────────────┐ │
│          │  │ Input Area (floating)                   │ │
│          │  └─────────────────────────────────────────┘ │
├──────────┴──────────────────────────────────────────────┤
│ StatusBar (h-28) — Connection, model, context, tokens   │
└─────────────────────────────────────────────────────────┘
```

### Key Dimensions

- Sidebar: 220px width, left corners rounded (8px)
- Titlebar: 40px height
- StatusBar: 28px height
- Max content width: 768px for chat messages
- Input box height: 48px

## Shadows

| Token | Value | Usage |
|-------|-------|-------|
| `--shadow-subtle` | `0 1px 2px rgba(17,17,23,0.05)` | Titlebar, statusbar |
| `--shadow-card` | `0 0px 3px rgba(17,17,23,0.05)` | Cards, settings items |
| `--shadow-sidebar` | `1px 0 4px rgba(17,17,23,0.05)` | Sidebar right edge |
| `--shadow-input` | `0 2px 0 #111111` | Input boxes (hard shadow) |

## Components

### Buttons

**Primary Button** (`btn-primary`)
- Background: `var(--accent)` or `var(--pure-blue)`
- Text: white
- Border-radius: 6px
- Padding: 7px 14px
- No shadow

**Ghost Button** (`btn-ghost`)
- Background: `var(--surface-2)`
- Text: `var(--fg-secondary)`
- Border: none
- Border-radius: 6px

### Input Fields (`input-field`)

- Background: white
- Border: 2px solid `var(--fg)`
- Border-radius: 6px
- Padding: 0 16px
- Hard shadow: `0 2px 0 #111111`
- Height: 48px

### Cards (`surface-card`)

- Background: white
- Border: 1px solid `var(--border)`
- Border-radius: 6px
- Subtle shadow: `0 0 3px rgba(17,17,23,0.05)`

### Toggle Switch

- Width: 44px, Height: 24px
- Border-radius: 12px
- Active: `var(--pure-blue)` background, white dot
- Inactive: `#d4d4d4` background, white dot

## Chat Interface

### Message Types

| Type | Style | Behavior |
|------|-------|----------|
| User | "YOU" label (Anton, accent), no background | Always visible |
| Assistant | "ASSISTANT" label (Anton, muted), no background | Always visible |
| System | Mono font, muted color | Collapsed by default |
| Tool | Tool card with status indicator | Collapsed when done |

### Tool Card

- Header: tool icon + name + status badge
- Body: JetBrains Mono font, diff syntax highlighting
- Border: 1px solid `var(--border)`
- Border-radius: 6px

### Empty State

- Centered layout
- "WHAT ARE WE BUILDING?" heading (42px, Anton)
- Description text (16px, Inter)
- Quick action cards (2x3 grid) with icons
- Recent sessions list

### Input Area

- 48px height, 2px black border, hard shadow
- Red send button
- HUD row: checkpoint info + undo/redo/history buttons

## Sidebar Navigation

### Structure

1. **New Chat** button (top, blue, rounded)
2. **Recent Sessions** (inline list)
3. **Divider**
4. **Nav Items**: Chat, Knowledge, Skills, MCP, Memory, Plugins
5. **System Items**: Settings

### Nav Item States

- Default: `var(--fg-muted)` text, transparent background
- Hover: `var(--fg)` text, `var(--surface-2)` background
- Active: `var(--fg)` text, `var(--surface-2)` background, accent icon
- Sidebar has left-side rounded corners (8px)

## Pages

### Chat (Empty)
- Welcome section with Anton heading
- Quick action cards (Debug, Refactor, Test, Branch, Document, Architect)
- Recent sessions list

### Chat (Active)
- User/assistant message bubbles
- Tool cards with status badges
- Input area with HUD

### Knowledge
- Document table (name, chunks, size, date)
- Search bar + Upload button
- Document icons colored by type

### Skills
- Skill cards with toggle switches
- Call count statistics
- Enabled/disabled badges

### MCP Servers
- Server cards with status badges (green/yellow/red)
- Tool tags (neutral background)
- Enable/disable/reload actions

### Memory
- Key-value entry list
- Section headers (User Preferences, Project Context)
- Update timestamps

### Plugins
- Plugin cards with install/configure buttons
- Version badges
- Description text

### Settings
- Sectioned layout (General, Model, Appearance)
- List items with labels and values
- Chevron-right indicators

## Motion

- Transition duration: 150ms ease for hover states
- Button press: `transform: scale(0.98)`
- Collapsible: chevron rotation
- Respects `prefers-reduced-motion: reduce`

## Accessibility

- Focus ring: 2px solid accent, 2px offset
- Reduced motion: all animations disabled
- WCAG AA contrast minimum
- Semantic HTML throughout
- Keyboard navigation supported

## Anti-Patterns (Avoid)

- No neon/outer glows
- No gradient text
- No decorative status dots
- No section-numbering eyebrows
- No card-heavy layouts where spacing would work
- No soft shadows (use hard shadows for Brutalist style)
