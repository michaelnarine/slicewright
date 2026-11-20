// SPDX-License-Identifier: AGPL-3.0-only
#include "errors.hpp"

#include <cxxabi.h>

#include <nanobind/stl/string.h>

#include <cstdlib>
#include <memory>
#include <new>
#include <typeinfo>

#include "libslic3r/Config.hpp"
#include "libslic3r/Exception.hpp"
#include "libslic3r/Print.hpp"

namespace slicewright {

namespace {

ErrorClasses *g_classes = nullptr;

nb::object make_class(nb::module_ &m, const char *name, const nb::object &base, std::initializer_list<const char *> attrs)
{
    std::string qualified = std::string("slicewright_engine.") + name;
    PyObject *cls = PyErr_NewException(qualified.c_str(), base.ptr(), nullptr);
    if (!cls)
        throw nb::python_error();
    nb::object obj = nb::steal(cls);
    // Documented attributes always exist; they are None until a raise site fills them in.
    obj.attr("message") = nb::str("");
    for (const char *a : attrs)
        obj.attr(a) = nb::none();
    m.attr(name) = obj;
    return obj;
}

std::string demangled(const std::type_info &ti)
{
    int status = 0;
    std::unique_ptr<char, void (*)(void *)> name(abi::__cxa_demangle(ti.name(), nullptr, nullptr, &status), std::free);
    return (status == 0 && name) ? std::string(name.get()) : std::string(ti.name());
}

} // namespace

ErrorClasses &errors() { return *g_classes; }

void set_error(const nb::object &cls, const std::string &message, Attrs attrs)
{
    nb::object exc = cls(message.c_str());
    exc.attr("message") = nb::str(message.c_str());
    for (const auto &kv : attrs)
        exc.attr(kv.first) = kv.second;
    PyErr_SetObject(cls.ptr(), exc.ptr());
}

void raise(const nb::object &cls, const std::string &message, Attrs attrs)
{
    set_error(cls, message, attrs);
    throw nb::python_error();
}

void raise_config_error(const std::string &message, const std::string &key, const std::string &value)
{
    raise(errors().ConfigError, message,
          {{"key", key.empty() ? nb::object(nb::none()) : nb::object(nb::str(key.c_str()))},
           {"value", value.empty() ? nb::object(nb::none()) : nb::object(nb::str(value.c_str()))}});
}

// What a nanobind exception translator has to do: set the Python error and return. Throwing from it (what raise()
// does) makes nanobind report a generic RuntimeError that carries the text of the intended exception.
static void set_config_error(const std::string &message)
{
    set_error(errors().ConfigError, message, {{"key", nb::none()}, {"value", nb::none()}});
}

void bind_errors(nb::module_ &m)
{
    static ErrorClasses classes;
    g_classes = &classes;
    classes.Error = make_class(m, "Error", nb::borrow(PyExc_Exception), {});
    classes.Cancelled = make_class(m, "Cancelled", classes.Error, {});
    classes.Busy = make_class(m, "Busy", classes.Error, {});
    classes.StateError = make_class(m, "StateError", classes.Error, {"state"});
    classes.ConfigError = make_class(m, "ConfigError", classes.Error, {"key", "value"});
    classes.ValidationError = make_class(m, "ValidationError", classes.Error, {"opt_key", "object_name", "issues"});
    classes.SliceError = make_class(m, "SliceError", classes.Error, {"object_name"});
    classes.ArrangeError = make_class(m, "ArrangeError", classes.Error, {"object_names"});
    classes.EngineError = make_class(m, "EngineError", classes.Error, {"detail"});

    // No C++ exception crosses into Python unmapped (04 section 7). nanobind consults translators for
    // anything it does not handle itself (python_error and its own builtin exceptions are not affected).
    nb::register_exception_translator(
        [](const std::exception_ptr &p, void *) {
            try {
                std::rethrow_exception(p);
            } catch (const nb::python_error &) {
                throw;
            } catch (const nb::builtin_exception &) {
                throw;
            } catch (const std::bad_alloc &) {
                throw;  // nanobind maps this to MemoryError
            } catch (const Slic3r::UnknownOptionException &e) {
                set_config_error(e.what());
            } catch (const Slic3r::BadOptionTypeException &e) {
                set_config_error(e.what());
            } catch (const Slic3r::ConfigurationError &e) {
                set_config_error(e.what());
            } catch (const Slic3r::PlaceholderParserError &e) {
                set_config_error(e.what());
            } catch (const Slic3r::CanceledException &) {
                set_error(errors().Cancelled, "cancelled");
            } catch (const Slic3r::SlicingError &e) {
                set_error(errors().SliceError, e.what(), {{"object_name", nb::none()}});
            } catch (const Slic3r::SlicingErrors &e) {
                set_error(errors().SliceError, e.errors_.empty() ? std::string(e.what()) : std::string(e.errors_.front().what()),
                          {{"object_name", nb::none()}});
            } catch (const std::exception &e) {
                set_error(errors().EngineError, e.what(), {{"detail", nb::str(demangled(typeid(e)).c_str())}});
            }
        },
        nullptr);

    // Test hook (private, unversioned): throws the named native exception on the calling thread, so the tests can
    // check what the translator makes of each.
    m.def("_throw_native", [](const std::string &kind, const std::string &message) {
        if (kind == "configuration") throw Slic3r::ConfigurationError(message);
        if (kind == "unknown_option") throw Slic3r::UnknownOptionException(message);
        if (kind == "bad_option_type") throw Slic3r::BadOptionTypeException(message);
        if (kind == "placeholder") throw Slic3r::PlaceholderParserError(message);
        if (kind == "canceled") throw Slic3r::CanceledException();
        if (kind == "slicing") throw Slic3r::SlicingError(message);
        if (kind == "slicing_errors") throw Slic3r::SlicingErrors(std::vector<Slic3r::SlicingError>{Slic3r::SlicingError(message)});
        if (kind == "runtime") throw std::runtime_error(message);
        throw nb::value_error("unknown kind");
    }, nb::arg("kind"), nb::arg("message") = "boom");
}

} // namespace slicewright
