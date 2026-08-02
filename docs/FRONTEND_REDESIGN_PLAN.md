# Comet Frontend Redesign Plan

## Product Assessment

Comet has a strong product foundation: chat, knowledge retrieval, long-term memory,
research, automation, and model configuration form a coherent personal AI workspace.
The current UI is functional and has meaningful empty, loading, and task states, but
it still exposes the history of several visual directions at once.

### Strengths

- The primary workflows are present and reachable from a shared workspace shell.
- Async work is visible through document states and the global work queue.
- Desktop and mobile visual regression coverage already exists for core workflows.
- The warm workspace direction suits a personal knowledge product better than a
  generic dark developer console.

### Design Issues To Resolve

1. **Competing visual systems**: warm yellow tokens coexist with older blue,
   glass, grid, and gradient overrides. This makes the product feel assembled
   instead of authored.
2. **Weak hierarchy in sparse states**: knowledge and memory pages leave large
   inactive areas without giving the user an obvious next action or useful status.
3. **Dashboard priority is diluted**: operational metrics, onboarding, and entry
   points have comparable visual weight. The first action should be clearer.
4. **Navigation overload**: the sidebar is useful for experts but too dense for a
   new user. Primary work, automation, resources, and configuration need stable
   visual grouping and quieter secondary items.
5. **Inconsistent semantics**: the accent color is used for primary actions,
   warnings, selected navigation, and decorative surfaces. Status needs a separate
   semantic palette.

## Product Direction

**Comet is a calm desktop workbench for turning personal material into useful AI
work.** It should feel dependable, editable, and information-rich without looking
like a marketing page or a collection of floating cards.

- Warm ivory canvas, paper-white working surfaces, ochre as the only action accent.
- Dense but breathable desktop layout: 8px spacing rhythm, restrained shadows,
  8px radii, and clear section headers.
- A page always answers: what is here, what needs attention, and what can I do next.
- Status colors are reserved for status; decoration does not compete with actions.
- Mobile preserves task completion, not desktop density.

## Delivery Phases

### Phase 1: Foundations And Workbench Shell

- Consolidate global tokens and remove conflicting legacy visual treatments.
- Refine navigation, header, focus states, cards, tables, forms, and task drawer.
- Reframe the dashboard around readiness, current work, and direct next actions.
- Update desktop and mobile visual baselines.

### Phase 2: Core Workflows

- Redesign Chat for conversation focus, source/tool legibility, and robust error states.
- Redesign Knowledge Base and detail pages around ingestion progress, retrieval
  readiness, and material management.
- Redesign Memory and Graph around evidence, confidence, and relationship exploration.

### Phase 3: Operations And Configuration

- Align Research, agent tasks, traces, global search, favorites, and media library.
- Make model, skill, tool, persona, and notification settings easier to audit and operate.
- Standardize empty/loading/error states and responsive behavior across every route.

### Phase 4: Product Acceptance

- Add visual baselines for all remaining high-traffic routes at desktop and mobile sizes.
- Validate keyboard focus, overflow, loading, error, and empty states.
- Run build and visual regression suites, then deploy the web container.

## Acceptance Criteria

- No functional API contract or workflow changes.
- A single warm design system controls global surfaces, typography, spacing, and states.
- Every primary route has an intentional desktop and mobile layout.
- Visual regression checks cover the redesigned workflow before phase completion.

## Completion Snapshot

All four delivery phases are complete for the authenticated product workspace.

- Phase 1 delivered the warm desktop shell, navigation hierarchy, dashboard, and work queue.
- Phase 2 delivered the chat, knowledge, memory, and graph workspaces.
- Phase 3 delivered research, scheduled tasks, global search, model configuration, and tool operations.
- Phase 4 aligned the remaining group chat, traces, favorites, image library, music, profile, persona, skill, and notification routes.

The visual suite now covers 22 primary authenticated routes at desktop and mobile
viewports (44 screenshots). Data-rich mocks exercise the core workflows; remaining
support routes use empty-state baselines to ensure their loading and no-data states
stay usable without inventing business data. API contracts and workflow behavior are
unchanged by this redesign.
