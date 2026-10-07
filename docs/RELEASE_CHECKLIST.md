# Public release checklist

This candidate was exported into a separate folder with a new Git
repository. The live bot, its source, private repository history and data were
not changed. The distribution repository is `Szof1e/Szofie-Bot`. Its original
placeholder README commit is retained as the parent of the public source release;
the live bot's private history is not imported. Publication does not deploy a bot,
register Discord commands or change existing player records.

Implemented release preparation:

- Removed account/server/channel-specific game outcomes and special logistics.
- Removed altered casino-stat displays and permanent wealth-display projections.
- Removed historical attack-log display exclusions and hidden prefix utilities.
- Replaced deployment-specific channel/role identifiers with configurable settings.
- Excluded all live data, credentials, logs, backups and private scripts.
- Added 184 runtime media files, a checksum inventory and provenance/rights notices.
  Excluded raw references, personal coalition art, prompts and obsolete variants.
  Removed PNG textual/EXIF metadata without changing pixel data.
- Kept ordinary owner permissions, public NYX and the current gameplay additions.
- Removed obsolete one-off deployment migrations; new installs use current defaults
  and later administrator customisations are not overwritten by old migrations.
- Added source/credential safeguards and offline tests for equal treatment and
  missing-art fallbacks.

Before publishing:

1. Review the selected MIT licence and project attribution.
2. Review dependency and artwork notices. Third-party artwork redistribution rights
   are not independently cleared; obtain permission or replace affected media.
3. Run `python scripts/check.py` and check only intended files are staged.
4. Smoke-test on a separate Discord test bot/server with a new token and empty data.
5. Connect an account with write access to the selected public repository.
   Publish this clean source tree, never the live bot's Git history.

Validation: 678 offline tests passed on Python 3.9 and Python 3.12. Syntax, Ruff,
generated-catalogue consistency, production confidentiality checks and the
public-release source/staging safeguard passed. The identifier cross-check found
no original deployment account/server/channel identifiers in distributed source,
tests or documentation. The media inventory matches the bundled files, all media
passes image integrity checks and every attachment is below 8 MiB. GIF timing is
preserved. These are integrity/attachment checks, not a fresh visual art review.

Offline tests use mocked Discord responses. The candidate has not connected to
Discord and has not been deployed over the live bot. Security safeguards are
defence in depth, not a formal security audit or a guarantee of zero bugs.
