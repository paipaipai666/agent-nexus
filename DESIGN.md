---
name: AgentNexus
description: A local-first AI agent platform with FSM-driven safety and tool governance
colors:
  surface-0: "#0d1117"
  surface-1: "#161b22"
  surface-2: "#1c2128"
  surface-3: "#252b33"
  surface-4: "#2d333b"
  fg: "#e6edf3"
  fg-secondary: "#8b949e"
  fg-muted: "#484f58"
  fg-faint: "#30363d"
  accent: "#58a6ff"
  accent-hover: "#79c0ff"
  accent-muted: "rgba(88,166,255,0.15)"
  accent-subtle: "rgba(88,166,255,0.06)"
  green: "#3fb950"
  green-muted: "rgba(63,185,80,0.15)"
  red: "#f85149"
  red-muted: "rgba(248,81,73,0.15)"
  amber: "#d29922"
  amber-muted: "rgba(210,153,34,0.15)"
  blue: "#58a6ff"
  blue-muted: "rgba(88,166,255,0.15)"
  purple: "#bc8cff"
  purple-muted: "rgba(188,140,255,0.15)"
  cyan: "#39d3f5"
  cyan-muted: "rgba(57,211,245,0.15)"
  border: "rgba(255,255,255,0.10)"
  border-subtle: "rgba(255,255,255,0.04)"
  border-strong: "rgba(255,255,255,0.18)"
  light-surface-0: "#f6f8fa"
  light-surface-1: "#ffffff"
  light-surface-2: "#f6f8fa"
  light-surface-3: "#eaeef2"
  light-surface-4: "#d0d7de"
  light-fg: "#1f2328"
  light-fg-secondary: "#57606a"
  light-fg-muted: "#8c959f"
  light-fg-faint: "#afb8c1"
  light-accent: "#0969da"
  light-accent-hover: "#0550ae"
  light-accent-muted: "rgba(9,105,218,0.12)"
  light-accent-subtle: "rgba(9,105,218,0.06)"
  light-border: "rgba(27,31,36,0.12)"
  light-border-subtle: "rgba(27,31,36,0.04)"
  light-border-strong: "rgba(27,31,36,0.20)"
typography:
  display:
    fontFamily: "DM Sans, -apple-system, BlinkMacSystemFont, sans-serif"
    fontWeight: 600
    lineHeight: 1.2
  body:
    fontFamily: "DM Sans, -apple-system, BlinkMacSystemFont, sans-serif"
    fontWeight: 400
    fontSize: "0.875rem"
    lineHeight: 1.6
  label:
    fontFamily: "DM Sans, -apple-system, BlinkMacSystemFont, sans-serif"
    fontWeight: 500
    fontSize: "0.75rem"
    letterSpacing: "0.02em"
  mono:
    fontFamily: "Fira Code, Consolas, monospace"
    fontWeight: 400
    fontSize: "0.8125rem"
    lineHeight: 1.5
  serif:
    fontFamily: "Source Serif 4, Georgia, serif"
    fontWeight: 400
    fontSize: "0.9375rem"
    lineHeight: 1.7
rounded:
  sm: "4px"
  md: "6px"
  lg: "8px"
  xl: "12px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
  "2xl": "48px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.fg-secondary}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  input-field:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.fg}"
    rounded: "{rounded.md}"
    padding: "8px 12px"
  sidebar:
    backgroundColor: "{colors.surface-1}"
    width: "52px"
  card:
    backgroundColor: "{colors.surface-2}"
    rounded: "{rounded.md}"
---

# Design System: AgentNexus

## 1. Overview

**Creative North Star: "The Control Room"**

AgentNexus is a command center for AI agents. The interface borrows from control room aesthetics: dense information panels, clear status indicators, sharp typography, and a restrained color vocabulary where every hue means something. The design prioritizes readability and signal-to-noise over decoration.

The system rejects the generic SaaS dashboard template: no card grids with uniform spacing, no stock hero sections, no flat gray-on-white styling. It also rejects the overly dark terminal aesthetic that feels niche and intimidating. The goal is earned familiarity: a tool that looks like it was built by engineers who use it daily.

**Key Characteristics:**
- **High contrast** between text and surfaces. Body text is never muted gray on a tinted background.
- **Dual theme** with equal care given to light and dark modes. Neither is an afterthought.
- **Information density** balanced with breathing room. Dense where users scan fast (tables, logs, status), spacious where they read or write (chat, prose, forms).
- **Monospace as a first-class citizen** for data, logs, session IDs, and technical readouts. Not an afterthought.
- **Dashed separators** instead of solid lines for visual rhythm. They suggest structure without heaviness.

## 2. Colors: The Signal Palette

The palette is built on high-contrast neutrals with a vivid blue/cyan accent that signals action, selection, and status. Every color has a job; none is decorative.

### Primary
- **Electric Blue** (`#58a6ff` / dark, `#0969da` / light): The action color. Used for primary buttons, active selections, links, and the current focus indicator. Appears on no more than 10% of any screen. Its rarity is the point.

### Secondary
- **Signal Cyan** (`#39d3f5`): Secondary accent for data visualization, charts, and complementary indicators. Used sparingly alongside Electric Blue, never competing with it.

### Neutral (Dark Theme)
- **Deep Charcoal** (`#0d1117`): The deepest background. Used only for the outermost surface (body, backdrop).
- **Panel Dark** (`#161b22`): The primary surface. Sidebars, main content area, elevated panels.
- **Card Dark** (`#1c2128`): Elevated surfaces. Cards, input fields, dropdowns, tool panels.
- **Hover Dark** (`#252b33`): Interactive hover states, selected rows, active list items.
- **Bright White** (`#e6edf3`): Primary text. High contrast against dark surfaces (ratio ≥ 13:1).
- **Muted Gray** (`#8b949e`): Secondary text. Labels, descriptions, timestamps. Still ≥ 4.5:1 against Panel Dark.

### Neutral (Light Theme)
- **Clean White** (`#ffffff`): Primary surface. Cards, input fields, panels.
- **Off-White** (`#f6f8fa`): Body background and secondary surfaces. Not pure white to reduce eye strain.
- **Border Gray** (`#d0d7de`): Borders and dividers. Visible but not heavy.
- **Ink** (`#1f2328`): Primary text. Near-black, high contrast against white (ratio ≥ 15:1).
- **Slate** (`#57606a`): Secondary text. Labels, descriptions.

### Semantic
- **Success Green** (`#3fb950`): Completed states, success indicators, connected status.
- **Error Red** (`#f85149`): Errors, failures, disconnected status, destructive actions.
- **Warning Amber** (`#d29922`): Warnings, pending states, queued items, rate limits.
- **Info Blue** (`#58a6ff`): Informational messages, links, active selections.

### Named Rules

**The Signal Rule.** Every color on screen must have a job. If a color exists because "it looks nice" or "the template had it," remove it. The palette has four roles: surface, text, accent, semantic. No fifth.

**The 10% Rule.** The primary accent (Electric Blue) appears on ≤10% of any given screen. Its rarity is what makes it a signal. If every interactive element is blue, nothing is.

**The Contrast Floor.** Body text must hit ≥ 7:1 against its background (WCAG AAA). Large text (≥18px or bold ≥14px) needs ≥ 4.5:1. Placeholder text needs the same ratio as body text; never the muted-gray default.

## 3. Typography

**Display Font:** DM Sans (with system fallback)
**Body Font:** DM Sans (with system fallback)
**Serif Font:** Source Serif 4 (with Georgia fallback) — used for markdown prose and agent responses
**Mono Font:** Fira Code (with Consolas fallback) — used for data, logs, session IDs, code

**Character:** DM Sans is a geometric sans with humanist warmth. It carries headings, labels, buttons, and navigation with equal clarity at small sizes. Source Serif 4 provides contrast for long-form reading (agent responses, markdown content). Fira Code is the technical voice: monospace for data, logs, and code.

### Hierarchy
- **Display** (600, `clamp(1.5rem, 4vw, 2.5rem)`, 1.2): Page titles, hero headings. Used sparingly; most pages don't need display type.
- **Headline** (600, `1.375rem`, 1.25): Section headings, dialog titles, panel headers.
- **Title** (500, `1rem`, 1.35): Card titles, list group headers, form section labels.
- **Body** (400, `0.875rem`, 1.6): Primary reading text. Max line length 65–75ch for prose. Denser for data tables.
- **Label** (500, `0.75rem`, 1.4, `letter-spacing: 0.02em`): Buttons, tags, status labels, navigation items.
- **Mono** (400, `0.8125rem`, 1.5): Session IDs, timestamps, log output, code blocks, diff display.
- **Serif** (400, `0.9375rem`, 1.7): Agent responses, markdown body content. Serif for reading, sans for interface.

### Named Rules

**The One-Family Rule.** DM Sans carries all interface text. Serif and mono are specialized voices (reading and data), not alternatives for the same role. If a heading uses DM Sans at 600 weight, don't switch to Source Serif 4 for variety.

**The Scale Rule.** The type scale uses fixed rem values, not fluid clamp. Users view at consistent DPI; a fluid heading that shrinks in a sidebar looks wrong, not responsive. Exception: page-level display headings may use clamp for dramatic sizing.

**The Serif-for-Reading Rule.** Agent responses and markdown prose use Source Serif 4. Interface elements (labels, buttons, navigation, data) use DM Sans. The serif says "slow down and read"; the sans says "act on this."

## 4. Elevation

AgentNexus uses **tonal layering** rather than shadows for depth. Surfaces are differentiated by background color (darker = deeper, lighter = elevated), not by drop shadows. This approach is native to dark UI where shadows are less visible anyway.

The exception: **modal overlays and dropdowns** use a subtle shadow (`0 8px 32px rgba(0,0,0,0.4)` in dark, `0 8px 32px rgba(0,0,0,0.08)` in light) to lift them above the surface stack. These shadows are structural, not decorative.

### Surface Stack (Dark Theme)
- **Level 0** (`#0d1117`): Body, backdrop, the deepest layer.
- **Level 1** (`#161b22`): Sidebar, main content area, primary panels.
- **Level 2** (`#1c2128`): Cards, input fields, elevated content blocks.
- **Level 3** (`#252b33`): Hover states, selected items, active tabs.
- **Level 4** (`#2d333b`): Pressed states, deeper hover.

### Named Rules

**The Tonal Rule.** Depth is conveyed by surface color, not shadow. Darker = deeper. Lighter = elevated. If you reach for `box-shadow` to show depth, use a surface color change instead.

**The Shadow Exception.** Shadows exist only for elements that float above the surface stack: modals, dropdowns, popovers, tooltips. These are the only elements that "leave the ground."

## 5. Components

### Buttons
- **Shape:** Gently curved edges (6px radius). Compact but not cramped.
- **Primary:** Electric Blue background (`#58a6ff`), white text, 8px 16px padding. Subtle glow on hover (`box-shadow: 0 0 12px rgba(88,166,255,0.2)`). Scale to 0.97 on active press.
- **Ghost:** Transparent background, muted text, 1px border. Border brightens on hover. Used for secondary actions.
- **Icon:** 36px square, centered icon, no border. Used in toolbars and sidebar navigation.
- **Destructive:** Error Red background. Used only for irreversible actions (delete, clear, reset).

### Sidebar Navigation
- **Style:** 52px wide icon rail. No text labels; icons carry the meaning with tooltip on hover.
- **Active state:** Electric Blue accent icon, subtle blue background tint, 2px accent indicator bar on the left edge.
- **Hover state:** Background shifts to Level 3 surface, icon brightens to secondary text color.
- **Divider:** Dashed line separating primary navigation (Chat, Knowledge, Skills, MCP, Memory, Plugins) from utility (Settings, Stats, Health, Alerts, Audit, Eval).

### Cards / Containers
- **Corner Style:** Gently rounded (6px radius).
- **Background:** Level 2 surface (`#1c2128` dark, `#f6f8fa` light).
- **Border:** 1px solid border color. Visible but not heavy.
- **Internal Padding:** 16px standard, 12px compact.
- **No shadows** for cards. Depth via surface color.

### Inputs / Fields
- **Style:** Level 1 surface background, 1px border, 6px radius, 8px 12px padding.
- **Focus:** Border shifts to accent color, subtle accent glow ring (`box-shadow: 0 0 0 1px var(--accent), 0 0 16px var(--accent-muted)`).
- **Placeholder:** Muted text color, not the faint default. Must maintain ≥ 4.5:1 contrast.

### Chat Messages
- **User messages:** Accent-tinted background with a 2px solid accent left border. Compact padding, rounded right corners.
- **Agent responses:** Full-width, serif font (Source Serif 4), rendered as markdown. No background tint; they flow as prose.
- **Tool cards:** Terminal-style panels with monospace font. Header shows tool name and status indicator (green dot = done, amber = running, red = error). Body shows output or diff with syntax coloring.
- **System messages:** Monospace, muted color, used for status updates and command output.

### Command Palette
- **Trigger:** Typing `/` in the chat input.
- **Style:** Floating dropdown above input, Level 3 surface, 1px strong border, heavy shadow. Category headers with semantic color labels. Active item highlighted with accent background and left border.
- **Keyboard:** Arrow keys navigate, Tab/Enter selects, Escape dismisses.

### Status Bar (HUD)
- **Style:** Monospace 11px, dashed top border, muted text. Shows model name, context usage (tokens), input/output token counts, session checkpoint ID, undo/redo controls.
- **Status indicators:** Small colored dots (5px) with glow for model and checkpoint status.

### Named Rules

**The Dashed-Line Rule.** Structural separators (sidebar border, panel dividers, section breaks) use dashed borders, not solid. Dashed lines suggest organization without creating visual walls. Reserve solid borders for component outlines (cards, inputs, buttons).

**The Terminal-Card Rule.** Tool output, code, diffs, and log data use monospace font in a distinct surface-level panel with a colored header strip. These are the "instrument readouts" of the control room. They should feel like terminals, not like regular UI cards.

## 6. Do's and Don'ts

### Do:
- **Do** maintain ≥ 7:1 contrast ratio for body text against its background (WCAG AAA). Test both themes.
- **Do** use Electric Blue (`#58a6ff` dark / `#0969da` light) as the sole primary accent. Its rarity on screen is the signal.
- **Do** use dashed borders for structural separators. Solid borders are for component outlines only.
- **Do** render agent responses and markdown in Source Serif 4 at 15px. The serif signals "read this."
- **Do** render all data, logs, session IDs, timestamps, and code in Fira Code. The mono signals "technical data."
- **Do** use tonal layering (surface color) for depth, not shadows. Shadows are reserved for floating elements.
- **Do** use semantic colors (green, red, amber) for status indicators with matching glow effects.
- **Do** provide both light and dark themes with equal care. Neither is an afterthought.

### Don't:
- **Don't** use the generic SaaS dashboard template: card grids with uniform spacing, stock hero sections, flat gray-on-white styling. PRODUCT.md calls this out as the primary anti-reference.
- **Don't** use gradient text (`background-clip: text`). Use a single solid color. Emphasis via weight or size.
- **Don't** use side-stripe borders greater than 1px as colored accents on cards or callouts.
- **Don** use the hero-metric template (big number + small label + supporting stats). It's a SaaS cliché.
- **Don't** use identical card grids with icon + heading + text repeated endlessly.
- **Don't** put tiny uppercase tracked eyebrows above every section. One named kicker as a deliberate brand system is voice; an eyebrow on every section is AI grammar.
- **Don't** use numbered section markers (01 / 02 / 03) as default scaffolding.
- **Don't** use glassmorphism or decorative blur effects. They're rare and purposeful, or nothing.
- **Don't** use muted gray body text on tinted backgrounds. If the contrast is even close, bump the text toward the brighter end.
- **Don't** use more than 3 font families. DM Sans + Source Serif 4 + Fira Code is the complete set.
- **Don't** animate layout properties (width, height, margin, padding). Use transform, opacity, and compositor-friendly properties only.
- **Don't** gate content visibility on class-triggered transitions. Transitions pause on hidden tabs; the content ships blank.
