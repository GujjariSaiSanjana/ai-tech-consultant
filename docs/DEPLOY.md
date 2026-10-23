# Deploy (temporary, free): Render

There is no separate frontend to deploy: each app serves its own UI. Inbox is a Next.js app,
and Brain and Scope render their pages on the server.

`render.yaml` creates a free Postgres database and three free web services (Brain, Inbox, Scope) in
Singapore. It is meant for a short demo, not production.

## Steps

1. Render dashboard → **New** → **Blueprint**. Connect this GitHub repo and choose `main`.
2. When asked, fill in `GEMINI_API_KEY` (free at aistudio.google.com). `ANTHROPIC_API_KEY` and
   `NVIDIA_API_KEY` are optional; leave them blank if you don't have them.
3. Click **Apply** and wait for all three services to go live (the first build takes about 10 minutes).
4. Once the Brain is live (it creates the tables on start), load the help-center articles into the new
   database from a machine that already has them. Copy the database's **External Database URL** from
   its Render page:

   ```bash
   pg_dump --data-only --no-owner --no-acl --exclude-table=schema_migrations \
     --exclude-table=llm_calls --exclude-table=answer_feedback \
     postgresql://postgres:postgres@localhost:5432/brain | psql -v ON_ERROR_STOP=1 "<External Database URL>"
   ```

   No local copy? Run the crawl against it instead (about 25 minutes):
   `cd brain && BRAIN_DATABASE_URL="<External Database URL>" uv run python -m brain.crawl`.
5. Check `https://ai-tc-brain.onrender.com/health`: `articles` should be 1540.

The Brain UI asks for a password: it is the generated `BRAIN_API_KEY` (Render → env group
`ai-tc-shared`). If Render gave the Brain a different URL than `ai-tc-brain.onrender.com`, update
`BRAIN_URL` on all three services.

## What to expect on the free plan

- **Sleep:** each service sleeps after 15 idle minutes, and the first request then takes 30–60 s.
  Open all three links a minute before a demo.
- **Reseeding:** Inbox and Scope store SQLite on an ephemeral disk, so they reseed their demo data
  on every start. Anything you add is lost when a service restarts.
- **No nightly sync:** background workers aren't free, so help-center sync doesn't run.
- **Database expiry:** Render deletes free databases after a fixed period (30 days when this was
  written). Delete the services when the demo is over.
