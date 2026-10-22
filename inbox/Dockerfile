# Inbox Agent: Next.js app on Node 26 (node:sqlite is built in, no native modules).
# Build:  docker build -t inbox-agent .
# Run:    docker run -p 3000:3000 -v inbox-data:/app/data/runtime inbox-agent
# Seed:   docker run --rm -v inbox-data:/app/data/runtime inbox-agent npm run seed
FROM node:26-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Dev dependencies are kept: `npm run seed` and `npm run eval` use tsx, and next.config.ts is TypeScript.
RUN npm run build

FROM node:26-slim
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    INBOX_DATABASE_PATH=/app/data/runtime/inbox.db
COPY --from=build /app/package.json /app/package-lock.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/.next ./.next
COPY --from=build /app/data/emails.jsonl ./data/emails.jsonl
COPY --from=build /app/src ./src
COPY --from=build /app/scripts ./scripts
COPY --from=build /app/next.config.ts /app/tsconfig.json ./
RUN mkdir -p /app/data/runtime && chown -R node:node /app/data
USER node
EXPOSE 3000
VOLUME ["/app/data/runtime"]
CMD ["npm", "start"]
