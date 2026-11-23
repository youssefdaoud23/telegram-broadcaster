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
    Never infer or look up phone numbers, automatically import contacts,
    change opt-in, send messages, join groups or scan private chats.
    Explicit user-confirmed import of an existing owner-scoped scan into a
    private contact group is allowed and starts new contacts as consent=0.
    Preserve limits, cross-workspace protection and partial-result warnings.
    Do not scan real group data without an explicit user-selected group.


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


22. The user dislikes esoteric-looking symbols/logos in the optional
    Space Science theme. Do not restore planet/orbit/satellite motifs,
    star-dotted page backgrounds, ornamental preview icons, or glowing/
    rotated Space brand logos. Preserve a clean text-first banner,
    the familiar Telegram paper-plane brand and original Classic design.


23. Direct import from a scanned Telegram group uses
    POST /groups/scans/{token}/import. This creates/reuses a private
    contact group with the same name, reuses matching existing contacts
    safely, and restores previously archived matching contacts only through
    an explicit user-triggered import. Restored records reuse original IDs,
    set archived=0, enabled=0 and consent=0, with prior opt-in withdrawal
    audited when needed. Active contacts keep their consent; imports do not
    send messages. Telegram User IDs are retained in scan exports and
    may resolve only through the original sender's entity cache/access
    hash; never claim all users are messageable. The Contacts page also
    has an additive/removal bulk group membership action. Preserve
    cross-workspace/CSRF validation and the 5,000-member import bound.


23a. Contact management interface (2026-11-22): show available phone numbers
     and Telegram numeric IDs separately from usernames in Contacts and group
     member lists; never infer inaccessible phone numbers. Search on Contacts
     updates immediately for names, usernames, phone, numeric ID, notes,
     and group labels; group dropdown also filters locally without reload.
     Only visible contacts are selected by Select all. The group editor's
     bulky consent panel has been replaced with individual and whole-group
     Active/Inactive switches, backed by owner-scoped status routes changing
     only enabled, not consent, history, or memberships. Bulk status changes
     are also supported on Contacts. Existing verified messaging-permission
     controls remain available under Advanced on Contacts, because receiving
     a username or group membership is not automatic private-message consent.
     Sending still requires consent and enabled status; never bypass that.

23c. Group management and phone backfill (2026-11-22): group page is a
     scoped list of actual members only, with searchable Add contacts picker
     listing out-of-group contacts and a separate selected-member Remove
     action. POST /contacts/groups/{id}/members/{add|remove} uses the
     owner-scoped additive/removal membership helper; no implicit replace on
     save. Existing Active controls and phone/Telegram ID fields remain.
     On direct and CSV reimport, safely matched existing contacts may have
     missing phone, username or numeric ID filled only if the incoming
     identifier is present and non-conflicting; never overwrite nonempty
     fields, names, notes, consent or enabled. Telegram privacy may hide
     phone numbers, which cannot be recovered if absent in the source.

23b. Import-default policy (2026-11-22): direct Telegram scan imports and CSV
     imports create new contacts with enabled=0, even when CSV Enabled=yes.
     Explicit import restoration of archived records also uses enabled=0,
     consent=0 and preserves original IDs/history. Linking an existing
     unarchived record never changes its enabled status. Individual/group/
     selected Active toggles remain the only way to enable imported contacts
     after import. Keep separate permission verification enforcement.

24. A newly added bulk consent feature has selected-contact and whole-group
    operations. Explicitly verified direct individual permission and an
    evidence/source note are required for granting. Group membership,
    scanning and import never grant consent. Withdrawal is available
    without attestation and applies to each contact across all groups.
    Store auditable changes in contact_consent_events, owner scoped.
    Never claim the app independently verifies consent beyond the
    operator's attestation. Bulk contact removal is archive+disable,
    never physical deletion of campaign or delivery history. The
    Telegram worker rechecks consent before each send, but cannot
    retract already sent or in-flight messages.

25. Hardening (2026-11-23): the maintainer decided consent is given when
    a person hands over their number, so the single add/edit contact
    forms and CSV Opt In/Consent columns set consent WITHOUT a reason.
    Do not add evidence requirements there. Form changes are still logged
    silently to contact_consent_events (source contact_form). Bulk consent
    keeps its existing attestation. PeerFloodError and any FloodError pause the broadcast and
    leave remaining recipients pending. Failed sign-ins are throttled per
    (username, client IP) in login_failures (5 per pair, 20 per IP, 15 min);
    the legacy login_attempts table is unused. The login page tells the
    user how many attempts remain and, when locked, how many minutes
    remain (core.login_status). CLIENT_IP_HEADER (Cloudflare:
    CF-Connecting-IP) is honoured only from RFC1918/ULA/loopback peers.
    Production publishes port 8789 only on 172.19.0.1 (BROADCASTER_BIND_IP),
    the Cloudflare tunnel network's gateway, which the tunnel route uses.
