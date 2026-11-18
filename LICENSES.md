# Third-party licenses

This repository vendors third-party source for traceability. Vendored code is reference material. It is not the production implementation of LinkedIn Content OS.

## linkedin-agent-skill

| | |
| --- | --- |
| Upstream | https://github.com/Jakeschincariol/linkedin-agent-skill |
| Revision | `add2c23882fe79180737d242ff80a5da205eda6a` |
| Branch inspected | `main` |
| License | MIT |
| Copyright | Copyright (c) 2026 Jake Schincariol |
| Vendored | 2026-09-23 |
| Location | `vendor/linkedin-agent-skill/` |
| Modifications | None. The snapshot excludes `.git` and was copied without edits. |

The MIT license requires the copyright notice and permission notice to stay with copies or substantial portions of the software. The unmodified license text is:

`vendor/linkedin-agent-skill/LICENSE`

Attribution: Jake Schincariol, https://opusjake.ai. Upstream write-up: https://opusjake.ai/r/linkedin-agent.

Do not remove or rewrite that license file when updating the vendor snapshot.

## Adapted portions

P2 adapts these files. The vendor snapshot itself is still unmodified.

| Project path | Upstream source | What changed |
| --- | --- | --- |
| `.cursor/skills/li-human/humanize.py` | `skills/li-human/humanize.py` | Dashes are kept. Typography rewrites only entries marked `rewrite`. |
| `.cursor/skills/li-human/detect.py` | `skills/li-human/detect.py` | Scores are reported. Em dashes are not scored. Exit status is always 0. |
| `.cursor/skills/li-human/slop.json` | `skills/li-human/slop.json` | Invisible-character table kept. Lexicon replaced with the voice avoid-list. |
| `.cursor/skills/li-post/hooks.json` | `skills/li-post/hooks.json` | Templates and traps kept. Performance rankings removed. |

The MIT permission notice for those adaptations is in `.cursor/skills/li-human/NOTICE.md`. Hook examples are illustrations of shape. They are not facts about the author of this repository.
