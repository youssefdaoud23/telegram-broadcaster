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
    Telegram groups exposed to the user's connected account and exports
    Name, Username and Phone Number only when those fields are visible
    in Telethon User objects. Hidden phone numbers must remain blank.
    Never infer or look up phone numbers, import contacts, change opt-in,
    send messages, join groups or scan private chats. Preserve limits,
    cross-workspace protection and partial-result warnings. Do not scan
    real group data without an explicit user-selected group.


14. The contact database remains SQLite under /opt/telegram-broadcaster/data.
    Contact groups are owner-scoped many-to-many associations, not Telegram
    chat groups. A contact can be in several groups, but consent and active
    status are always checked separately for every broadcast. Ensure all
    new routes respect authenticated owner IDs, and preserve existing
    contacts, campaign history, and SQLite data during migrations.
15. CSV Groups names are semicolon-separated and must match existing groups.
    Do not automatically import Telegram group members into contact groups
    or mark them opted in. Overlapping selected groups are unioned to
    distinct contact IDs before drafting a message. Use SQLite online
    backup and verify no sending jobs before rebuilding production.


16. General public docs must support third-party fresh installs without
    assuming /opt, n8n, Cloudflare or a particular domain. Use
    scripts/configure.py with Docker Compose. Never silently swap storage
    from bind mount to named volume for an existing installation.
17. Preserve the original /opt production .env, encryption key, SQLite
    and Telegram sessions until an authorized verified migration.
    Compose default port fallback preserves legacy production behavior,
    but new .env.example defaults to localhost.
18. Keep README and docs/INSTALL.md, CONFIGURATION.md, OPERATIONS.md,
    SECURITY.md, TROUBLESHOOTING.md current. GitHub CI never sends
    real Telegram messages or accesses existing account data.
19. No LICENSE has been selected by the maintainer. Do not silently grant
    license rights without explicit approval.


20. The site supports an optional Space Science-inspired template; Telegram
    Classic remains the default. Keep all Space styling scoped to
    root data-site-template="space", including responsive rules and
    day/night palettes. Never import the original template's third-party
    imagery or code. Theme preference is persisted per website user via
    admins.site_template and only changed by authenticated CSRF-checked
    POST /settings/appearance. Do not change broadcasting state when
    changing appearance. Login remains on default Classic presentation
    until a website session is authenticated.
21. UI work is developed independently from production. Before any live
    /opt upgrade, verify no active sends and make a consistent backup.
    Keep the other Docker services, SQLite data, and Telegram sessions intact.
