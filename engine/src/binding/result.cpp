// SPDX-License-Identifier: AGPL-3.0-only
#include "result.hpp"

#include <nanobind/ndarray.h>
#include <nanobind/stl/shared_ptr.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <boost/filesystem.hpp>

#include "errors.hpp"
#include "moves.hpp"

namespace slicewright {

namespace fs = boost::filesystem;

SliceResult::~SliceResult()
{
    boost::system::error_code ec;
    if (!gcode_path.empty())
        fs::remove(gcode_path, ec);
}

std::string SliceResult::write_gcode(const std::string &path) const noexcept
{
    try {
        boost::system::error_code ec;
        fs::copy_file(gcode_path, path, fs::copy_options::overwrite_existing, ec);
        if (ec)
            return "cannot write " + path + ": " + ec.message();
        return {};
    } catch (const std::exception &e) {
        return "cannot write " + path + ": " + e.what();
    }
}

namespace {

// A read-only numpy array over `data`, owned by a capsule that keeps the whole ResultStore alive (zero copy,
// 04 section 5: "sharing one native buffer, freed when the last array and the result are gone").
template <typename T> nb::object view(const std::shared_ptr<ResultStore> &store, const T *data, size_t rows, size_t cols = 0)
{
    auto *holder = new std::shared_ptr<ResultStore>(store);
    nb::capsule owner(holder, [](void *p) noexcept { delete static_cast<std::shared_ptr<ResultStore> *>(p); });
    const size_t shape[2] = {rows, cols};
    nb::ndarray<nb::numpy, const T> arr(data, cols ? 2 : 1, shape, owner);
    return nb::cast(arr);
}

nb::dict moves_dict(const std::shared_ptr<ResultStore> &s)
{
    const ResultStore &r = *s;
    nb::dict d;
    d["position"] = view(s, r.position.data(), r.k, 3);
    d["type"] = view(s, r.type.data(), r.k);
    d["role"] = view(s, r.role.data(), r.k);
    d["filament"] = view(s, r.filament.data(), r.k);
    d["nozzle"] = view(s, r.nozzle.data(), r.k);
    d["color_id"] = view(s, r.color_id.data(), r.k);
    d["width"] = view(s, r.width.data(), r.k);
    d["height"] = view(s, r.height.data(), r.k);
    d["mm3_per_mm"] = view(s, r.mm3_per_mm.data(), r.k);
    d["feedrate"] = view(s, r.feedrate.data(), r.k);
    d["actual_feedrate"] = view(s, r.actual_feedrate.data(), r.k);
    d["fan"] = view(s, r.fan.data(), r.k);
    d["temperature"] = view(s, r.temperature.data(), r.k);
    d["pressure_advance"] = view(s, r.pressure_advance.data(), r.k);
    d["acceleration"] = view(s, r.acceleration.data(), r.k);
    d["jerk"] = view(s, r.jerk.data(), r.k);
    d["time"] = view(s, r.time.data(), r.k, 2);
    d["layer_id"] = view(s, r.layer_id.data(), r.k);
    d["print_z"] = view(s, r.print_z.data(), r.k);
    d["object_id"] = view(s, r.object_id.data(), r.k);
    d["gcode_line"] = view(s, r.gcode_line.data(), r.k);
    return d;
}

nb::dict layers_dict(const std::shared_ptr<ResultStore> &s)
{
    nb::dict d;
    d["z"] = view(s, s->layer_z.data(), s->layers);
    d["first"] = view(s, s->layer_first.data(), s->layers);
    d["last"] = view(s, s->layer_last.data(), s->layers);
    return d;
}

} // namespace

void bind_result(nb::module_ &m)
{
    // Test hook (private, unversioned): moves converted per chunk; 0 restores the default. Lets the tests put chunk
    // boundaries inside a small slice.
    m.def("_set_move_chunk", [](size_t n) { set_move_chunk(n); }, nb::arg("n"));
    nb::class_<SliceResult>(m, "SliceResult", nb::is_weak_referenceable())
        .def_prop_ro("gcode_path", [](const SliceResult &r) { return r.gcode_path; })
        .def_prop_ro("objects", [](const SliceResult &r) { return r.objects; })
        .def_prop_ro("wipe_tower", [](const SliceResult &) { return nb::none(); })
        .def_prop_ro("moves", [](const SliceResult &r) { return moves_dict(r.store); })
        .def_prop_ro("layers", [](const SliceResult &r) { return layers_dict(r.store); })
        .def_prop_ro("gcode_line_ends", [](const SliceResult &r) { return view(r.store, r.store->line_ends.data(), r.store->line_ends.n); })
        .def_prop_ro("warnings", [](const SliceResult &r) { return issue_list(r.warnings); })
        .def_prop_ro("stats",
                     [](const SliceResult &r) {
                         // Partial until M5 layer 6 (04 section 5.4): totals only.
                         nb::dict t, d, s;
                         t["normal"] = r.time_normal;
                         t["silent"] = r.time_silent;
                         d["estimated_normal_print_time"] = nb::str(r.time_display.c_str());
                         s["time_s"] = t;
                         s["layer_count"] = r.layer_count;
                         s["threads"] = r.threads;  // the arena the job actually ran in (set_threads, clamped to the machine)
                         s["display"] = d;
                         return s;
                     })
        .def(
            "write_gcode",
            [](const SliceResult &r, const std::string &path) {
                std::string error;
                {
                    nb::gil_scoped_release release;
                    error = r.write_gcode(path);
                }
                // Only now, with the GIL held again, may a Python exception object be built.
                if (!error.empty())
                    raise(errors().EngineError, error, {{"detail", nb::str("filesystem")}});
            },
            nb::arg("path"));
}

} // namespace slicewright
