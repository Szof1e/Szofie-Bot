# Third-party notices

The project's MIT licence covers project source, not its dependencies, trademarks
or third-party media. Dependencies are installed separately by pip; their complete
licence files travel with their distributions. No dependency source or binary is
vendored in this repository. Preserve the applicable licences/notices if you
redistribute a bundle containing these packages.

The runtime list below is based on `requirements.lock` and locally installed
package metadata. Conditional packages not installed in the tested interpreter
are marked unverified rather than assigned a guessed licence.

| Package | Locked version | Declared licence |
| --- | --- | --- |
| discord.py | 2.7.1 | MIT, copyright Rapptz |
| python-dotenv | 1.2.1 | BSD-3-Clause |
| Pillow | 11.3.0 | MIT-CMU |
| aiohappyeyeballs | 2.6.1 | PSF-2.0 |
| aiohttp | 3.13.5 | Apache-2.0 AND MIT |
| aiosignal | 1.4.0 | Apache-2.0 |
| async-timeout | 5.0.1 | Verify distribution notices (Python below 3.11) |
| attrs | 26.1.0 | MIT |
| audioop-lts | 0.2.2 | Verify distribution notices (Python 3.13+) |
| frozenlist | 1.8.0 | Apache-2.0 |
| idna | 3.18 | BSD-3-Clause |
| multidict | 6.7.1 | Apache-2.0 |
| propcache | 0.4.1 | Apache-2.0 |
| typing_extensions | 4.16.0 | PSF-2.0 |
| tzdata | 2026.3 | Apache-2.0; bundled timezone data has its own notices |
| yarl | 1.22.0 | Apache-2.0 |

Development tools in `requirements-dev.txt` are also separately installed and
retain their own licences. Review their distributions when bundling tools.

Runtime artwork is included separately from the MIT-licensed code. See
[artwork notices](assets/ARTWORK_NOTICES.md) and the machine-readable
[inventory](assets/artwork-manifest.json) for provenance groups and file hashes.
The media includes fan illustrations, reference-edited variants and legacy clips.
Underlying third-party rights have not been independently cleared; neither
attribution nor the MIT code licence grants those rights. Character, ship,
franchise and manufacturer references do not imply endorsement or ownership.
