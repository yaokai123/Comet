# Frontend Design System

## Purpose

Comet uses a warm desktop-workbench visual language. New UI should compose existing primitives and semantic tokens before adding page-specific CSS.

## Semantic Tokens

The source of truth is `web/src/styles/design-system.css`.

- Surfaces: `--comet-bg`, `--comet-surface`, `--comet-surface-raised`, `--comet-surface-subtle`
- Text and borders: `--comet-text`, `--comet-muted`, `--comet-subtle`, `--comet-border`, `--comet-border-strong`
- Intent: `--comet-primary`, `--comet-success`, `--comet-warning`, `--comet-danger`, `--comet-info`
- Layout: `--comet-space-1` through `--comet-space-6`, `--comet-radius-*`, `--comet-shadow-card`

Use intent tokens for status and controls. Domain-specific colors, such as knowledge-base labels or persona avatars, may remain data-driven but must not be used as global UI colors.

## Primitives

- `PageHeader`: page title, concise description, and command area.
- `IconActionButton`: named icon-only action with a tooltip and accessible label.
- `WorkspaceState`: standard loading, empty, error, and disabled content states.

Keep page-specific layouts in the matching page stylesheet. Do not add global overrides to make a local component look correct.

## State Matrix

Every data workspace must intentionally provide the following states:

| State | Required behavior |
| --- | --- |
| Loading | Preserve page structure, announce progress, and disable conflicting primary commands. |
| Empty | Explain what is missing and provide one clear next action. |
| Populated | Keep primary content, status, and actions scannable at desktop and mobile widths. |
| Error | State what failed and provide a retry path without losing navigation. |
| Disabled | Explain why an unavailable action cannot run and how to recover when possible. |

The visual suite verifies the knowledge workspace across populated, loading plus disabled action, empty, and error states. Reuse that pattern when adding a data-backed page.

## Delivery Gate

Before merging a UI change:

1. Use semantic tokens and an existing primitive where it fits.
2. Add or update desktop and mobile visual coverage for the changed state.
3. Run `npm.cmd run build` and `npm.cmd run test:visual` from `web/`.
