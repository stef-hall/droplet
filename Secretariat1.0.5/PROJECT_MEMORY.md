# Project Memory

Last updated: 2026-05-06

## What this project is
- Name: `Server1.0.1`
- Type: Python web app
- Main backend file: `server.py`
- Frontend files: `templates/index.html`, `templates/styles.css`, `static/app.js`

## Current direction
- User wants to rebuild a "nice" website experience.
- Preferred next step: choose a page type and implement it fast.

## Decisions
- Keep a persistent memory file in-repo so context survives across chats.
- Update this file after each meaningful change.

## Open tasks
- Recover or redesign the intended website layout.
- Pick one initial target:
  - Landing page
  - Portfolio
  - Dashboard
  - Product page

## Session notes
- 2026-05-06: User asked for persistent memory to avoid forgetting context.
- 2026-05-06: Added live backend-driven status updates for chat processing (`Thinking...`, `Getting Events...`, `Adding Event...`, `Deleting...`) via streamed API responses.
- 2026-05-20: Added shared sticky notes with a flip-to-share panel, collaborator username syncing, mirrored shared note files, and low-frequency polling for cross-user note updates.

## How to use this memory
- At session start: read this file first.
- After edits: append a short note with date and what changed.
- When priorities change: update "Current direction" and "Open tasks".
