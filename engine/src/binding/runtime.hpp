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

// The process-wide engine lock of 04 section 9 rule 3 (a job from start() to its terminal state, an arrange() call).
// acquire_engine() returns false when it is taken.
bool acquire_engine();
void release_engine();

// Scoped ownership of the engine lock: held (and released on every exit, a throw included) if the constructor got it.
//     EngineLock lock;
//     if (!lock) raise(errors().Busy, ...);
class EngineLock {
public:
    EngineLock() : m_held(acquire_engine()) {}
    ~EngineLock()
    {
        if (m_held)
            release_engine();
    }
    EngineLock(const EngineLock &) = delete;
    EngineLock &operator=(const EngineLock &) = delete;
    explicit operator bool() const { return m_held; }

private:
    bool m_held;
};

void bind_runtime(nb::module_ &m);

} // namespace slicewright
