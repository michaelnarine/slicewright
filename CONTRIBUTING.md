# Contributing

Thanks for helping. This repository holds two differently licensed parts, so a few rules matter more than usual. The full rationale is in [docs/publishing/compliance.md](docs/publishing/compliance.md).

## Licences by directory

| Directory | Licence | SPDX header on every file you write |
|---|---|---|
| `engine/` | AGPL-3.0-only | `SPDX-License-Identifier: AGPL-3.0-only` |
| `addon/` | GPL-3.0-or-later | `SPDX-License-Identifier: GPL-3.0-or-later` |
| everything else (docs, CI, tooling) | GPL-3.0 (root `LICENSE`) | optional |

The Orca submodule, the profile JSON and the patch files under `engine/patches/` keep their upstream licensing and are excluded from the header check.

## Provenance rule

- Code under `addon/` is written from the design docs and tests. **Never translate code from OrcaSlicer, BambuStudio, PrusaSlicer or libvgcode into `addon/`**, even loosely or "for reference".
- You may use behaviour descriptions in your own words, colour values, protocol facts and file-format facts.
- If something genuinely has to follow Orca's C++ line by line, it belongs in `engine/` (AGPL), e.g. `engine/src/glue/`.
- Don't move code from `engine/` to `addon/` unless you wrote all of it yourself.

PR descriptions include a checkbox confirming this; reviewers check it.

## Developer Certificate of Origin

Every commit must be signed off under the [Developer Certificate of Origin 1.1](https://developercertificate.org/):

```
git commit -s -m "Your message"
```

This adds `Signed-off-by: Your Name <you@example.com>`, certifying that you have the right to submit the change under the licence of the directory it touches. A CI check rejects commits without it.

## Pull requests

- Large changes land as stacks of small PRs (`gh stack`); see [the implementation plan](docs/implementation/plan.md).
- API changes follow the order in [04 §11](docs/design/04-engine-api.md#11-conformance).
- PRs that change anything visible in Blender include screenshots.
