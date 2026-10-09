#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Runs inside quay.io/pypa/manylinux_2_28_x86_64.
set -euxo pipefail
yum install -y autoconf automake libtool m4 texinfo perl-IPC-Cmd perl-Data-Dumper >/dev/null
/opt/python/cp312-cp312/bin/pip install -q cmake ninja
export PATH=/opt/python/cp312-cp312/bin:$PATH
git config --global --add safe.directory '*'
cd /work
bash spike/scripts/deps_unix.sh orca deps-out
