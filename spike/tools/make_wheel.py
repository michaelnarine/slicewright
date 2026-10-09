# SPDX-License-Identifier: AGPL-3.0-only
"""Assemble a cp312-abi3 wheel around the built extension. Usage: make_wheel.py <module-file> <platform-tag> <outdir>"""
import base64
import hashlib
import sys
import zipfile
from pathlib import Path

mod, plat, outdir = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
name, ver = "slicewright_engine", "0.0.0.dev0"
tag = f"cp312-abi3-{plat}"
dist = f"{name}-{ver}.dist-info"
files = {
    mod.name: mod.read_bytes(),
    f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {ver}\nLicense-Expression: AGPL-3.0-only\n".encode(),
    f"{dist}/WHEEL": f"Wheel-Version: 1.0\nGenerator: spike\nRoot-Is-Purelib: false\nTag: {tag}\n".encode(),
}
rec = []
for n, data in files.items():
    h = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    rec.append(f"{n},sha256={h},{len(data)}")
rec.append(f"{dist}/RECORD,,")
files[f"{dist}/RECORD"] = ("\n".join(rec) + "\n").encode()
outdir.mkdir(parents=True, exist_ok=True)
out = outdir / f"{name}-{ver}-{tag}.whl"
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for n, data in files.items():
        z.writestr(n, data)
print(out)
