# SPDX-License-Identifier: AGPL-3.0-only
"""The type stub must be exactly the code block in docs/design/04-engine-api.md section 3.

Process rule (04 section 11): the doc changes first, then the stub. This test
fails if they drift apart in either direction.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "design" / "04-engine-api.md"
STUB = REPO / "engine" / "python" / "slicewright_engine" / "__init__.pyi"
SPDX = "# SPDX-License-Identifier: AGPL-3.0-only\n"


def doc_block() -> str:
    text = DOC.read_text(encoding="utf-8")
    section = text.split("## 3. Module surface", 1)[1]
    m = re.search(r"```python\n(.*?)\n```", section, re.S)
    assert m, "no python block found in 04 section 3"
    return m.group(1) + "\n"


def test_stub_has_spdx_header_then_doc_block():
    stub = STUB.read_text(encoding="utf-8")
    assert stub.startswith(SPDX)
    assert stub[len(SPDX):] == doc_block()


def test_doc_block_declares_api_version():
    assert "API_VERSION: tuple[int, int] = (1, 0)" in doc_block()
