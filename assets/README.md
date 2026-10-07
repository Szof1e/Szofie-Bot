# Runtime artwork

184 runtime media files are bundled in this local release candidate. These are
the existing game illustrations and clips, not a newly generated replacement set.
The [inventory](artwork-manifest.json) records every included file's size, SHA-256
and provenance group. Read [artwork notices](ARTWORK_NOTICES.md) before publishing
or reusing this collection; the artwork is excluded from the MIT code licence.

| Directory | Used for |
| --- | --- |
| `plushies/` | Themed plushie collections and purchase embeds |
| `items/` | Android 21 Autonomous Helper |
| `strategic/` | Conventional vehicles, jets, strikes and defenses |
| `icbm/`, `aa/`, `nuke/` | Launch, interception and channel-recreation media |
| `thor/` | Module fabrication, launch, assembly, loading, release and counters |
| `imperial_star_destroyer/` | Construction, launch, assembly and Cinder events |
| `death_star/` | Construction, operation, charging, impact and recovery |
| `nyx/` | Fabrication, ready, launch, orbit, ability and interception |
| `space/`, `space/fleet/` | Exploration, transport, colonies and fleet lifecycle scenes |

Existing command filenames and fallbacks are preserved. Older craft intentionally
reuse their established illustration for stages without dedicated art; this does
not claim that a unique image exists for every possible command state. Missing
local media falls back to text/embeds; `/plushies` can fall back to a list. Disable
media using the associated `economy.*_media` or `moderation.nuke_media` settings.

Excluded: original reference photographs/downloads, generation prompts and local
paths, personal coalition banners, preview/contact sheets, desktop metadata and
superseded GR-75 loading scenes. PNG textual/EXIF metadata was stripped without
changing pixel data. GIF frames and timing remain unchanged.

Every bundled attachment is below 8 MiB. Image integrity and inventory hashes
are tested offline. This does not verify third-party licences, guarantee visual
accuracy or substitute for testing actual uploads in your Discord server.
