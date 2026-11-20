// SPDX-License-Identifier: AGPL-3.0-only
// Exception hierarchy of 04 section 7 and the mapping of native exceptions onto it.
#pragma once

#include <nanobind/nanobind.h>

#include <initializer_list>
#include <string>
#include <utility>

namespace slicewright {

namespace nb = nanobind;

// The Python classes, created once at module initialisation.
struct ErrorClasses {
    nb::object Error, Cancelled, Busy, StateError, ConfigError, ValidationError, SliceError, ArrangeError, EngineError;
};
ErrorClasses &errors();

// Creates the classes on module `m` and registers the C++ exception translator.
void bind_errors(nb::module_ &m);

using Attrs = std::initializer_list<std::pair<const char *, nb::object>>;

// Sets the Python error `cls(message)` (with `message` and the given attributes on the instance) without throwing: what
// an exception translator does. Everything else uses raise().
void set_error(const nb::object &cls, const std::string &message, Attrs attrs = {});

// Raises `cls(message)` with `message` and the given attributes set on the instance. Never returns.
[[noreturn]] void raise(const nb::object &cls, const std::string &message, Attrs attrs = {});

[[noreturn]] void raise_config_error(const std::string &message, const std::string &key = {}, const std::string &value = {});

} // namespace slicewright
