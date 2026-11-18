# Project license

Status: **pending a decision by the copyright holder**. This file is not a license grant.

The original engine, skills, tests, and docs were written for this project. Vendored and adapted third-party code stays under the MIT license already recorded in [LICENSES.md](../LICENSES.md) and `.cursor/skills/li-human/NOTICE.md`. Those notices have to remain no matter which project license is chosen.

No `LICENSE` file is present on purpose. Publishing the repository before adding one leaves the default copyright situation, which is a poor public release.

## Recommendation

MIT is the closer fit if the goal is a simple public portfolio repository:

- The vendored upstream is MIT, so the repository stays one license family plus the existing notices.
- The code is a single-author local tool. Recipients mainly need the right to use, modify, and redistribute it.
- The patent grant in Apache-2.0 is useful for larger projects. It also adds NOTICE and change-tracking expectations that this repository does not otherwise need.

Apache-2.0 is the better fit if an explicit patent grant matters more than matching the upstream license. It is compatible with keeping the MIT notices for the vendored files.

Either license can cover the original code. Neither license makes the synthetic demo claims true, and neither license authorizes publishing a LinkedIn post.

Proposed copyright line, to confirm before use: `Copyright (c) 2026 Juan José Sánchez Arroyo`.

## If MIT is chosen

Add `LICENSE` with the text below, keep `LICENSES.md`, and mention MIT in the README.

```text
MIT License

Copyright (c) 2026 Juan José Sánchez Arroyo

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## If Apache-2.0 is chosen

Use the Apache License, Version 2.0, from the Apache Software Foundation, with the same copyright holder in the source headers you choose to add. Do not remove the MIT license file under `vendor/linkedin-agent-skill/`. A short `NOTICE` should point at `LICENSES.md` for the vendored MIT material.

The Apache-2.0 text is longer than this note. Copy it from the Apache Software Foundation rather than from memory when the decision is made: https://www.apache.org/licenses/LICENSE-2.0

## Until then

Do not publish this candidate as an open-source release. The code, the tests, and the synthetic fixtures can be reviewed locally.
