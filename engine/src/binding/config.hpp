// SPDX-License-Identifier: AGPL-3.0-only
// Config functions of 04 section 6: config_schema, normalize_config, compose_config, eval_condition, ConditionContext.
#pragma once

#include <nanobind/nanobind.h>

#include "libslic3r/PrintConfig.hpp"

namespace slicewright {

namespace nb = nanobind;

// DynamicPrintConfig::apply() copies values into the options of the target. The options inside full_print_config()
// (and a DynamicPrintConfig that is filled from the definition) have a null keys_map for enum vectors (02 section 5.1),
// so a value copied into them serialises to nothing: the key vanished from normalize_config's output, from the config a
// job is built with and from the G-code's config block (z_hop_types, extruder_type, nozzle_type, nozzle_volume_type,
// ...). Enum vectors are therefore taken over as whole options, which carry the keys_map the loader gave them. Use
// instead of cfg.apply(layer, true) wherever a loaded layer goes onto such a config.
void apply_layer(Slic3r::DynamicPrintConfig &cfg, const Slic3r::DynamicPrintConfig &layer);

void bind_config(nb::module_ &m);

} // namespace slicewright
