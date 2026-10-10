// SPDX-License-Identifier: AGPL-3.0-only
// SliceJob and SliceResult (04 sections 4, 5, 8). M2 implements the build, run and wait paths; the rest of the
// surface (arrange, thumbnails, painting, the SoA move arrays, the 3MF writer) arrives with M5 and is declared
// so that the module has the documented shape.
#pragma once

#include <nanobind/nanobind.h>

namespace slicewright {

namespace nb = nanobind;

void bind_job(nb::module_ &m);

} // namespace slicewright
