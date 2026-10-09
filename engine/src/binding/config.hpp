// SPDX-License-Identifier: AGPL-3.0-only
// Config functions of 04 section 6: config_schema, normalize_config, compose_config, eval_condition, ConditionContext.
#pragma once

#include <nanobind/nanobind.h>

namespace slicewright {

namespace nb = nanobind;

void bind_config(nb::module_ &m);

} // namespace slicewright
