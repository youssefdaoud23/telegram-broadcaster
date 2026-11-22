# Continue Telegram Broadcaster Project

Before making any changes:
1. Read project_context.md, plan.md, README.md and recent Git history.
2. Inspect actual GitHub branch and server runtime before modifications.
3. Develop and test on a feature branch in the canonical repo https://github.com/youssefdaoud23/telegram-broadcaster.
4. Run unit tests and offline UI checks, review for secrets, then merge only verified changes into main.
5. Server updates always use git fetch + git pull --ff-only; never overwrite the local .env, data volume, Telegram sessions or contact DB.
6. Keep project_context.md and plan.md current; document fixes and open risks.
7. Never change the legacy n8n or personal Telegram bridge, and never send a Telegram message without explicit user approval.

8. Multi-user data is isolated by admins.id in SQL and worker checks. New endpoints must enforce owner_id and active role; never query across workspaces or reuse Telegram session IDs.
9. Before deploying a schema migration, require a tested SQLite online backup, verify the target legacy admin mapping, and avoid restarts during active sends.
10. Typography: Avoid Unicode em dashes (U+2014) throughout code, HTML, CSS, JavaScript, and documentation. Use the ASCII hyphen (-) instead.