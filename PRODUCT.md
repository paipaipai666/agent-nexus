# Product

## Register

product

## Users

AgentNexus serves three overlapping audiences:

1. **Solo developers building with AI** — value local-first simplicity, no cloud dependency, fast iteration. They want an agent that works out of the box without API key management or infrastructure setup.

2. **Security-conscious teams** — need audit trails, RBAC, tool governance, and data sovereignty. They care about deterministic agent behavior and the 7-layer security gate model.

3. **AI researchers and explorers** — experiment with agent architectures, care about observability, evaluation, and the FSM state machine. They want to understand *why* the agent made each decision.

All three share a context: they're technically sophisticated, value transparency over magic, and distrust black-box AI tools that hide their reasoning.

## Product Purpose

AgentNexus exists to prove that a local-first AI agent can be both powerful and safe. It's a ReAct (Thought→Action→Observe) single-agent tool that runs entirely on the user's machine — no data leaves the device. The FSM (16 states, 25 transitions) makes agent behavior deterministic and auditable, unlike prompt-driven agents that are unpredictable.

Success looks like: a developer installs it, runs `nexus init`, and has a fully functional AI agent with memory, knowledge base, browser automation, and tool governance — all without sending a single byte to the cloud.

## Brand Personality

**Precise. Technical. Trustworthy.**

The voice is direct, confident, and engineering-first. No marketing fluff, no buzzwords, no "seamless" or "empowering." The tool earns trust by showing its work — every reasoning step is traceable, every tool call is gated, every state transition is logged.

Emotional goals: confidence (the tool does what it says), clarity (the user always knows what's happening), control (the user can interrupt, override, or audit at any point).

## Anti-references

**Generic SaaS dashboard** — the typical sidebar + cards + charts template with no personality. AgentNexus should never look like a Notion clone, a Vercel dashboard knockoff, or a cookie-cutter admin panel. It has too much technical depth to dress up in generic UI patterns.

Specific patterns to avoid:
- Card grids with uniform spacing and no hierarchy
- Stock hero sections with gradient blobs
- Flat, gray-on-white styling with one accent color
- Dashboard-by-numbers layouts that feel like every other tool

## Design Principles

1. **Show, don't hide** — The FSM state machine, tool governance gates, and reasoning traces are the product's differentiators. Surface them prominently rather than burying them in settings. Transparency is a feature, not a debugging tool.

2. **Density over decoration** — The audience is technical and reads fast. Prefer information-dense layouts with clear hierarchy over spacious, decorative layouts that waste screen real estate. Every pixel should earn its place.

3. **Earn trust through craft** — The existing design system (copper/earth palette, noise texture, DM Sans + Source Serif 4) signals careful, deliberate work. Extend that attention to detail everywhere. A polished interface tells users the code underneath is equally polished.

4. **Local-first, not local-only** — The interface should feel like a premium desktop application, not a web app running in Electron. Respect platform conventions, use native-feeling interactions, and keep the UI responsive even when the agent is doing heavy computation.

5. **Practice what you preach** — AgentNexus is about deterministic, auditable AI. The interface should reflect that: consistent spacing scales, predictable interactions, no surprising animations or state changes. The user should always know what just happened and what happens next.

## Accessibility & Inclusion

Target WCAG AAA where practical, WCAG AA as minimum. Specific considerations:

- High contrast mode support for users who need it
- Full keyboard navigation for all interactive elements
- Screen reader compatibility with proper ARIA labels and live regions for agent status updates
- Reduced motion support — all animations must respect `prefers-reduced-motion`
- The existing copper/earth palette must maintain ≥7:1 contrast ratio for body text (AAA)
- Focus indicators must be clearly visible (the existing 2px accent outline is good)
