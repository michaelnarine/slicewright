// SPDX-License-Identifier: AGPL-3.0-only
#include "result.hpp"

#include <nanobind/ndarray.h>
#include <nanobind/stl/shared_ptr.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <boost/filesystem.hpp>

#include "errors.hpp"
#include "moves.hpp"
#include "runtime.hpp"

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

const char *const kBasenamePlaceholder = "SLICEWRIGHT_INPUT_BASENAME_PLACEHOLDER";

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

nb::dict pair_dict(const std::vector<std::pair<std::string, Stats::Pair>> &items)
{
    nb::dict d;
    for (const auto &kv : items) {
        nb::list pair;
        pair.append(kv.second[0]);
        pair.append(kv.second[1]);
        d[kv.first.c_str()] = pair;
    }
    return d;
}

// 04 section 5.4.
nb::dict stats_dict(const Stats &st)
{
    nb::dict s, time;
    time["normal"] = st.time_normal;
    time["silent"] = st.time_silent;
    s["time_s"] = time;
    s["prepare_time_s"] = st.prepare_time;
    s["time_by_role_s"] = pair_dict(st.time_by_role);
    s["time_by_move_type_s"] = pair_dict(st.time_by_move_type);
    nb::list filaments;
    for (const Stats::Filament &f : st.filaments) {
        nb::dict d;
        d["mm"] = f.mm;
        d["cm3"] = f.cm3;
        d["g"] = f.g;
        d["cost"] = f.cost;
        filaments.append(d);
    }
    s["filament_per_extruder"] = filaments;
    nb::dict per_role;
    for (const auto &kv : st.used_per_role) {
        nb::dict d;
        d["m"] = kv.second[0];
        d["g"] = kv.second[1];
        per_role[kv.first.c_str()] = d;
    }
    s["used_filament_per_role"] = per_role;
    nb::list flush;
    for (double g : st.flush_g)
        flush.append(g);
    s["flush_per_filament_g"] = flush;
    s["total_filament_changes"] = st.filament_changes;
    s["total_tool_changes"] = st.tool_changes;
    s["layer_count"] = st.layer_count;
    s["total_travel_mm"] = st.travel_mm;
    nb::dict display;
    for (const auto &kv : st.display)
        display[kv.first.c_str()] = nb::str(kv.second.c_str());
    s["display"] = display;
    s["threads"] = st.threads;  // the arena the job actually ran in (set_threads, clamped to the machine)
    return s;
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
        .def_prop_ro("wipe_tower",
                     [](const SliceResult &r) -> nb::object {
                         if (!r.wipe_tower.present)
                             return nb::none();
                         nb::dict d;
                         d["x"] = r.wipe_tower.x;
                         d["y"] = r.wipe_tower.y;
                         d["width"] = r.wipe_tower.width;
                         d["depth"] = r.wipe_tower.depth;
                         d["height"] = r.wipe_tower.height;
                         d["rotation_deg"] = r.wipe_tower.rotation_deg;
                         return d;
                     })
        .def(
            "output_filename",
            [](const SliceResult &r, const std::string &input_basename) {
                if (!r.filename_error.empty())
                    raise_config_error(r.filename_error, "filename_format");
                std::string name = r.filename_template;
                const std::string key = kBasenamePlaceholder;
                for (size_t pos = name.find(key); pos != std::string::npos; pos = name.find(key, pos + input_basename.size()))
                    name.replace(pos, key.size(), input_basename);
                return name;
            },
            nb::arg("input_basename"))
        .def_prop_ro("moves", [](const SliceResult &r) { return moves_dict(r.store); })
        .def_prop_ro("layers", [](const SliceResult &r) { return layers_dict(r.store); })
        .def_prop_ro("gcode_line_ends", [](const SliceResult &r) { return view(r.store, r.store->line_ends.data(), r.store->line_ends.n); })
        .def_prop_ro("warnings", [](const SliceResult &r) { return issue_list(r.warnings); })
        .def_prop_ro("stats", [](const SliceResult &r) { return stats_dict(r.stats); })
        .def(
            "write_gcode_3mf",
            [](const SliceResult &r, const std::string &path, nb::handle plate_meta) {
                std::string plate_name;
                if (!plate_meta.is_none()) {
                    if (!nb::isinstance<nb::dict>(plate_meta))
                        throw nb::type_error("plate_meta must be a dict or None");
                    nb::dict d = nb::borrow<nb::dict>(plate_meta);
                    if (d.contains("plate_name")) {
                        if (!nb::isinstance<nb::str>(d["plate_name"]))
                            throw nb::type_error("plate_meta['plate_name'] must be a str");
                        plate_name = nb::cast<std::string>(d["plate_name"]);
                    }  // other keys are ignored in v1 (04 section 5.1)
                }
                if (!r.plate)
                    raise(errors().EngineError, "this result has no plate data for a .gcode.3mf", {{"detail", nb::str("no plate")}});
                // 04 section 9 rule 3: the writer sets the process-global dialect flag, so it holds the engine lock (released on
                // every exit by the guard) and raises Busy while a job or arrange call has it.
                EngineLock engine;
                if (!engine)
                    raise(errors().Busy, "a job or arrange call is running (the 3MF writer sets process-global state)");
                std::string error;
                {
                    nb::gil_scoped_release release;
                    error = glue::write_gcode_3mf(*r.plate, path, plate_name);
                }
                if (!error.empty())
                    raise(errors().EngineError, error, {{"detail", nb::str("3mf")}});
            },
            nb::arg("path"), nb::arg("plate_meta") = nb::none())
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
