// SPDX-License-Identifier: AGPL-3.0-only
// slicewright_engine: the Python binding of the headless OrcaSlicer libslic3r (API 1.0, docs/design/04-engine-api.md).
#include <nanobind/nanobind.h>

#include "config.hpp"
#include "errors.hpp"
#include "job.hpp"
#include "runtime.hpp"

namespace nb = nanobind;

NB_MODULE(_native, m)
{
    m.doc() = "Slicewright engine: OrcaSlicer's libslic3r in-process (API 1.0)";
    slicewright::init_runtime();
    slicewright::bind_errors(m);
    slicewright::bind_runtime(m);
    slicewright::bind_config(m);
    slicewright::bind_job(m);
}
