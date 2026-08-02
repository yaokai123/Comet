# Frontend Acceptance Checklist

This checklist is the final manual gate for the Comet desktop workbench. Run it against a built, authenticated environment after automated visual regression passes.

## Shared Checks

- [ ] Navigation shows the current route, keeps the work queue reachable, and does not hide required actions.
- [ ] Loading, empty, populated, error, and permission-denied states communicate the next useful action.
- [ ] Primary, secondary, destructive, and status colors remain distinguishable without relying on color alone.
- [ ] Cards, drawers, dialogs, tables, and forms use the shared warm tokens without unexpected blue or purple visual overrides.
- [ ] Tab order is logical; every actionable icon can receive focus and has an accessible name.
- [ ] Enter and Space activate custom controls; Escape closes dialogs and drawers.

## Desktop (1440 px)

- [ ] Dashboard: overview cards, daily briefing, and global task drawer fit without overlap.
- [ ] Chat and group chat: conversation selection, message composer, tool detail, sharing, and long messages remain readable.
- [ ] Knowledge: create library, upload document, processing state, failure state, and detail navigation are clear.
- [ ] Memory and graph: filters, cards, relationship detail, graph canvas, and empty state remain legible.
- [ ] Research, scheduled tasks, search, models, and tools retain dense but scannable controls.
- [ ] Traces: filters, populated rows, detail drawer, timing, and cost information are readable.
- [ ] Favorites, images, music, profile, personas, skills, and notification channels show meaningful populated content and safe destructive actions.

## Mobile (390 px)

- [ ] Header actions, menu, search, and work queue do not overlap or clip.
- [ ] Side navigation and drawers are operable and close correctly.
- [ ] Cards reflow to one column where needed; tables and timelines preserve essential information without horizontal-page overflow.
- [ ] Dialogs, upload controls, segmented filters, and forms remain usable with touch targets of at least 40 px.
- [ ] Group chat and chat composer remain visible above the virtual keyboard.
- [ ] Music controls, image detail, trace detail, and notification actions remain reachable without hover.

## Support Page Scenarios

- [ ] Group chat: select a conversation, inspect tool details, compose a message, and verify member state.
- [ ] Traces: filter a populated list and open a trace detail drawer.
- [ ] Favorites: change type and presentation filters, open an item, and cancel a removal confirmation.
- [ ] Images: filter/search, open detail, toggle favorite, and cancel deletion.
- [ ] Music: open edit, use keyboard-accessible play/action controls, and cancel deletion.
- [ ] Profile: edit and cancel a nickname update; open and close password change.
- [ ] Personas: toggle a setting, activate a persona, and inspect group configuration.
- [ ] Skills: toggle visibility, edit a skill, and inspect a built-in template.
- [ ] Notifications: toggle a channel, run a test delivery, and cancel deletion.

## Sign-off

- [ ] `npm.cmd run build` passes.
- [ ] `npm.cmd run test:visual` passes for desktop and mobile projects.
- [ ] A reviewer completes the desktop and mobile checks above against the deployed environment.
