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

11. Production install target is /opt/telegram-broadcaster. If it remains under /root/n8n/telegram-broadcaster, use scripts/migrate_to_opt.sh from an authorized root Docker terminal. Never move only code or data, overwrite /opt target, or change n8n Compose. Require a verified SQLite backup, no active sends, then verify original counts and health.
12. Previous campaign reuse is an explicit new draft, not automatic message resend. Account/recipient consent and owner checks apply to reuse and edit. Submitted campaigns are immutable; only unsent drafts can be edited in place. Unknown or sent statuses require deliberate recipient selection and independent Send.

13. Group export is a read-only, owner-scoped tool at /groups. It reads only
    Telegram groups exposed to the user's connected account, exports only
    public usernames, and never imports contacts, changes opt-in, sends
    messages, joins groups or scans private chats. Preserve limits and
    partial-result warnings. Do not collect real member data without an
    explicit user-selected group.
