// SPDX-License-Identifier: AGPL-3.0-only
#pragma once

#include <nanobind/nanobind.h>

namespace slicewright {

namespace nb = nanobind;

void bind_testing(nb::module_ &m);

} // namespace slicewright
