// SPDX-License-Identifier: AGPL-3.0-only
// Module-level functions of 04 section 3 that are not config or job related: version, enums, log, directories,
// licences, and the CancelToken class.
#pragma once

#include <nanobind/nanobind.h>

#include <string>

namespace slicewright {

namespace nb = nanobind;

// Directory that holds the extension module and the data shipped with it (resources/, profiles.zip, ...).
const std::string &package_dir();

// Called first at import: sets Orca's resources and temporary directories and the default log level (04 section 3).
void init_runtime();

// Names of the move type and extrusion role values (the tables behind enums()); "Undefined" outside the range.
const char *move_type_name(int value);
const char *role_name(int value);

void bind_runtime(nb::module_ &m);

} // namespace slicewright
