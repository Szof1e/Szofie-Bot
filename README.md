# Szofie

A Python Discord game bot with a shared economy, casino games, careers, fishing,
coalitions, countries, strategic warfare and solar-system exploration.

This public source edition applies the same gameplay rules to every account.
Owner permissions are for administration, not better odds or free assets.
No player records, server configuration or previous repository history is included.

## Run your own bot

Use Python 3.12 for the validated development setup. Python 3.9 compatibility is
also checked. Runtime packages are pinned in `requirements.lock`.

1. Create an application at <https://discord.com/developers/applications> and create
   its bot token. Enable **Server Members Intent** and **Message Content Intent**.
2. Invite it using the `bot` and `applications.commands` OAuth scopes. Grant View
   Channels, Send Messages, Embed Links, Attach Files, Read Message History and
   Add Reactions. Grant Manage Channels only if you intend to use `/nuke`, which
   recreates a channel and permanently removes its messages.
3. Copy the example configuration and insert your own token:

   ```sh
   cp .env.example .env
   ```

   Never share the real `.env`. `OWNER_IDS` grants ordinary administrative access;
   it does not change game outcomes. `GUILD_ID` is optional development-server
   command sync; blank uses global registration. Global registration may take
   time to appear. `PATCHNOTES_CHANNEL_ID` is optional and only receives notes
   after explicit owner review and approval. Blank disables delivery.
4. Start the bot:

   ```sh
   bash run.sh
   ```

   The launcher installs the pinned runtime packages in a local `.venv` on first
   run. Use `/help` and `/config` after the bot connects. Economy policy is restricted
   to the server or bot owner. Configured admin/mod roles can manage other settings;
   configuration changes are audited. `/donutadmin` is an explicit, audited owner
   balance-adjustment command.

For Windows, use Python's virtual environment directly:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock
.venv\Scripts\python bot.py
```

## Gameplay and guides

- `/casino`: slots, wheel, roulette, blackjack and the casino tour.
- `/work` and `/job`: shifts, licences and helper progression.
- `/fish`, `/dive`, `/bestiary`: catches, contracts and specimen research.
- `/warfare`: vehicle preparation, ammunition, roles and counterplay.
- `/country guide`: countries, development, income and invasions.
- `/exploration` and `/space fleet guide`: travel, colonies, materials and fleets.
- `/thor guide`, `/isd guide`, `/deathstar guide`: megaproject rules and counters.
- `/arsenal`: privately inspect your own conventional and orbital equipment.

The [generated catalogue](docs/PUBLIC_CATALOG.md) lists default recipes. The
[fleet reference](docs/SPACE_FLEET.md) covers craft interactions. In-bot guides
use the active server settings where supported. Prices and probabilities are
game parameters, not financial advice or claims about real weapons.

NYX remains an ordinary purchasable counter-intelligence mechanic available to
all players; its temporary concealment is distinct from administrative permissions.
Raw-ID targets must be members of the current server; no cross-server targeting
privilege or hidden prefix casino is included.

## Persistence and operation

The bot creates `data/` on first use for JSON saves, ledgers and approval queues.
These directories are excluded from Git. Run one bot process per data directory;
do not run concurrent instances against the same JSON files. Back up `data/` while
the bot is stopped, or after a clean shutdown. Never copy another deployment's
player records into this source release. Resetting a server economy is destructive.

`ECON_SHARED_GUILDS` optionally shares player data across your own configured
servers; configuration and channels remain per-server. Leave it blank unless
you deliberately want that behaviour. Review permissions and attack rules before
inviting the bot to multiple communities.

## Artwork and licensing

This release includes 184 runtime artwork files for plushies,
vehicles, fleets, megaprojects, the helper item and launch/interception events.
Media attachments are enabled by default; missing files still use text/embed
fallbacks. See the [artwork inventory](assets/README.md) and
[artwork notices](assets/ARTWORK_NOTICES.md). Raw reference downloads, personal
coalition images and obsolete art variants are not included.

Some images are reference-edited fan artwork or legacy clips, and their underlying
third-party redistribution rights have not been independently cleared. This
package does not represent those images as MIT-licensed or rights-cleared. Review
the notices and obtain any necessary permissions or replace the affected art
before public distribution.

The project source is provided under the [MIT licence](LICENSE). Dependencies
retain their own licences; see [third-party notices](THIRD_PARTY_NOTICES.md).
The MIT grant does not grant rights to third-party characters, trademarks or
optional media. Fictional and real-world names remain part of the game; this
project is not endorsed by their respective owners.

## Development

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/check.py
```

Checks run offline: syntax, lint, public-source/credential safeguards, catalogue
consistency and ordinary gameplay regressions. They do not log into Discord or
register commands. See [development notes](docs/DEVELOPMENT.md) and the
[release checklist](docs/RELEASE_CHECKLIST.md).
