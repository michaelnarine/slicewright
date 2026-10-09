#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Runs inside quay.io/pypa/manylinux_2_28_x86_64: build module, check symbols/deps, make + audit wheel.
set -euxo pipefail
export PATH=/opt/python/cp312-cp312/bin:$PATH
pip install -q cmake ninja nanobind wheel auditwheel
git config --global --add safe.directory '*'
cd /work
bash spike/scripts/build_module_unix.sh /opt/python/cp312-cp312/bin/python
SO=$(ls build/slicewright_engine*.so)
mkdir -p dist
{
  echo '### nm -D --defined-only'; nm -D --defined-only "$SO"
  echo '### ldd'; ldd "$SO"
  echo '### objdump NEEDED'; objdump -p "$SO" | grep NEEDED
  echo '### GLIBC/GLIBCXX versions required'; objdump -T "$SO" | grep -oE 'GLIBC(XX)?_[0-9.]+|CXXABI_[0-9.]+' | sort -uV | tr '\n' ' '
  echo; echo '### size'; ls -l "$SO"
} | tee dist/linux_checks.txt
python spike/tools/make_wheel.py "$SO" linux_x86_64 dist/raw
auditwheel show dist/raw/*.whl | tee dist/auditwheel_show.txt
auditwheel repair --plat manylinux_2_28_x86_64 -w dist/wheelhouse dist/raw/*.whl | tee dist/auditwheel_repair.txt || true
ls -la dist dist/wheelhouse || true
