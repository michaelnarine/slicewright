// SPDX-License-Identifier: AGPL-3.0-only
#include "runtime.hpp"

#include <nanobind/stl/optional.h>
#include <nanobind/stl/string.h>

#include <atomic>
#include <cstdlib>
#include <fstream>
#include <sstream>

#include <boost/filesystem.hpp>
#include <boost/log/core.hpp>
#include <boost/log/expressions.hpp>
#include <boost/log/utility/setup/file.hpp>
#include <boost/log/utility/setup/common_attributes.hpp>

#ifdef _WIN32
#include <windows.h>
#include <process.h>
#else
#include <dlfcn.h>
#include <unistd.h>
#endif

#include "errors.hpp"
#include "version_info.hpp"

#include "libslic3r/ExtrusionEntity.hpp"
#include "libslic3r/GCode/GCodeProcessor.hpp"
#include "libslic3r/Utils.hpp"

namespace slicewright {

namespace fs = boost::filesystem;

namespace {

std::string locate_package_dir()
{
#ifdef _WIN32
    HMODULE h = nullptr;
    if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           reinterpret_cast<LPCSTR>(&locate_package_dir), &h)) {
        char buf[MAX_PATH * 4];
        if (GetModuleFileNameA(h, buf, sizeof(buf)))
            return fs::path(buf).parent_path().string();
    }
    return {};
#else
    Dl_info info;
    if (dladdr(reinterpret_cast<const void *>(&locate_package_dir), &info) && info.dli_fname)
        return fs::path(info.dli_fname).parent_path().string();
    return {};
#endif
}

std::string g_package_dir;
std::string g_temp_dir;
boost::shared_ptr<boost::log::sinks::sink> g_file_sink;

void remove_temp_dir()
{
    boost::system::error_code ec;
    if (!g_temp_dir.empty())
        fs::remove_all(g_temp_dir, ec);
}

std::string read_text(const fs::path &p, const char *placeholder)
{
    std::ifstream in(p.string(), std::ios::binary);
    if (!in)
        return placeholder;
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

std::vector<std::string> split(const std::string &s, char sep)
{
    std::vector<std::string> out;
    std::stringstream ss(s);
    std::string item;
    while (std::getline(ss, item, sep))
        if (!item.empty())
            out.push_back(item);
    return out;
}

struct CancelToken {
    std::atomic<bool> flag{false};
};

nb::dict enums()
{
    // Names are Orca's enumerators without their prefix. Numeric values follow Orca and may change on a
    // rebase without an API bump; callers map by name (04 section 5.2).
    static_assert(int(Slic3r::EMoveType::Count) == 11, "EMoveType changed: update enums()");
    static_assert(int(Slic3r::erCount) == 20, "ExtrusionRole changed: update enums()");
    static const char *const move_types[] = {"Noop", "Retract", "Unretract", "Seam", "Tool_change", "Color_change",
                                             "Pause_Print", "Custom_GCode", "Travel", "Wipe", "Extrude"};
    static const char *const roles[] = {"None", "Perimeter", "ExternalPerimeter", "OverhangPerimeter", "InternalInfill",
                                        "SolidInfill", "TopSolidInfill", "BottomSurface", "Ironing", "BridgeInfill",
                                        "InternalBridgeInfill", "GapFill", "Skirt", "Brim", "SupportMaterial",
                                        "SupportMaterialInterface", "SupportTransition", "WipeTower", "Custom", "Mixed"};
    nb::dict mt, rl, out;
    for (int i = 0; i < 11; ++i)
        mt[move_types[i]] = i;
    for (int i = 0; i < 20; ++i)
        rl[roles[i]] = i;
    out["move_type"] = mt;
    out["role"] = rl;
    return out;
}

nb::dict version()
{
    nb::dict build, deps, out;
    for (const std::string &kv : split(SW_DEPS, ';')) {
        auto eq = kv.find('=');
        if (eq != std::string::npos)
            deps[kv.substr(0, eq).c_str()] = nb::str(kv.substr(eq + 1).c_str());
    }
    build["compiler"] = SW_BUILD_COMPILER;
    build["platform"] = SW_BUILD_PLATFORM;
    build["date"] = SW_BUILD_DATE;
    build["deps"] = deps;
    nb::list patches;
    for (const std::string &p : split(SW_PATCHES, ';'))
        patches.append(p.c_str());
    out["version"] = SW_ENGINE_VERSION;
    out["api"] = nb::make_tuple(SW_API_MAJOR, SW_API_MINOR);
    out["orca_tag"] = SW_ORCA_TAG;
    out["orca_commit"] = SW_ORCA_COMMIT;
    out["patches"] = patches;
    out["build"] = build;
    out["source_url"] = SW_SOURCE_URL;
    return out;
}

void set_log(int level, const std::optional<std::string> &path)
{
    if (level < 0 || level > 5)
        throw nb::value_error("level must be in 0..5 (0 fatal, 1 error, 2 warning, 3 info, 4 debug, 5 trace)");
    namespace logging = boost::log;
    if (g_file_sink) {
        logging::core::get()->remove_sink(g_file_sink);
        g_file_sink.reset();
    }
    if (path && !path->empty()) {
        try {
            g_file_sink = logging::add_file_log(logging::keywords::file_name = *path,
                                                logging::keywords::open_mode = std::ios_base::app,
                                                logging::keywords::auto_flush = true);
            logging::add_common_attributes();
        } catch (const std::exception &e) {
            raise_config_error(std::string("cannot open the log file: ") + e.what(), "path", *path);
        }
    }
    Slic3r::set_logging_level(static_cast<unsigned>(level));
}

nb::dict licenses()
{
    // Placeholder until release packaging (M7) puts the real texts in the wheel: each file is read from the
    // package directory when present.
    const fs::path dir = g_package_dir;
    const char *missing = "(not packaged in this build)";
    nb::dict out;
    out["LICENSE"] = nb::str(read_text(dir / "LICENSE", missing).c_str());
    out["THIRD_PARTY_LICENSES"] = nb::str(read_text(dir / "THIRD_PARTY_LICENSES", missing).c_str());
    out["NOTICE"] = nb::str(read_text(dir / "NOTICE", missing).c_str());
    out["SOURCE"] = nb::str(read_text(dir / "SOURCE.txt", missing).c_str());
    return out;
}

} // namespace

const std::string &package_dir() { return g_package_dir; }

void init_runtime()
{
    g_package_dir = locate_package_dir();
    // Process-global directories, set once (02 section 4.2). Orca logs a non-fatal nozzle_info.json error on
    // every slice unless the resources directory points at the bundled data.
    Slic3r::set_resources_dir((fs::path(g_package_dir) / "resources").string());
#ifdef _WIN32
    const int pid = _getpid();
#else
    const int pid = getpid();
#endif
    boost::system::error_code ec;
    fs::path tmp = fs::temp_directory_path(ec) / "slicewright_engine" / std::to_string(pid);
    fs::create_directories(tmp, ec);
    g_temp_dir = tmp.string();
    Slic3r::set_temporary_dir(g_temp_dir);
    std::atexit(remove_temp_dir);
    Slic3r::set_logging_level(1);
}

void bind_runtime(nb::module_ &m)
{
    m.attr("API_VERSION") = nb::make_tuple(SW_API_MAJOR, SW_API_MINOR);
    m.def("version", &version);
    m.def("enums", &enums);
    m.def("set_log", &set_log, nb::arg("level"), nb::arg("path") = nb::none());
    m.def("licenses", &licenses);
    m.def("resources_dir", []() { return (fs::path(g_package_dir) / "resources").string(); });
    m.def("profiles_archive", []() { return (fs::path(g_package_dir) / "profiles.zip").string(); });

    nb::class_<CancelToken>(m, "CancelToken")
        .def(nb::init<>())
        .def("cancel", [](CancelToken &t) { t.flag.store(true); })
        .def_prop_ro("cancelled", [](const CancelToken &t) { return t.flag.load(); });
}

} // namespace slicewright
