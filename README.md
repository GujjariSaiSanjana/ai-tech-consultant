# AI Tech Consultant

One system that knows the product, handles the inbox, and turns client meetings into scopes of work.

| Folder | Task | Language |
|---|---|---|
| `brain/` | 1. Knowledge Brain: help-center memory, chatbot, API | Python |
| `inbox/` | 2. Inbox Agent: drafts replies that learn from every sent email | TypeScript |
| `scope/` | 3. Meeting to Scope of Work: agent team, estimate, proposal | Python |
| `evals/` | Shared eval reporter | Python |

Tasks 2 and 3 call Task 1 only over its API: see `docs/contracts/brain-api.md`.

Full run instructions land with each task.
