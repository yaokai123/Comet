# Comet Frontend Product Redesign Plan

## Product Positioning

Comet is a personal AI workspace for knowledge, memory, research, and agent execution. The interface should feel like a focused operating desk rather than a marketing site: clear navigation, visible system state, fast entry points, and enough visual richness to make the product feel alive.

## Design Direction

- Keep the existing Ant Design and React structure.
- Preserve all routes, API calls, stores, and user workflows.
- Use a brighter workspace palette built around blue, cyan, and neutral surfaces.
- Make richness come from product information: setup progress, content counts, task states, and recent activity.
- Keep dashboard cards compact with 8px radius and restrained shadows.
- Use subtle motion and hover states for affordance, not decoration.

## Implemented Scope

- Theme tokens refreshed in `web/src/theme.ts`.
- Global workspace visual layer updated in `web/src/index.css`.
- Main dashboard hero now surfaces product state: documents, conversations, entities, and setup progress.
- Login page now communicates the core product pillars: AI chat, knowledge RAG, and long-term memory.
- Web development nginx config remains HTTP-friendly for local Docker deployment.
- Desktop app shell added with Electron in `web/electron/`.
- Vite output now uses relative asset paths so the desktop app can load the built UI from disk.
- Electron uses HashRouter while the browser build keeps BrowserRouter.
- The latest visual direction is a warm yellow knowledge-workspace palette.

## Next Product Iterations

- Add page-level empty states for Knowledge, Memory, Research, and Traces.
- Add compact status chips in the global header for model readiness and background task state.
- Split large frontend bundle with route-level lazy loading.
- Add visual QA screenshots for desktop and mobile before release.
