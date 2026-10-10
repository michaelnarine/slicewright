// SPDX-License-Identifier: AGPL-3.0-only
#include "job.hpp"

#include <nanobind/ndarray.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/shared_ptr.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <tbb/blocked_range.h>
#include <tbb/info.h>
#include <tbb/parallel_for.h>
#include <tbb/task_arena.h>
#include <tbb/task_scheduler_observer.h>

#include <clocale>
#ifndef _WIN32
#include <locale.h>
#include <xlocale.h>
#endif

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <sstream>
#include <thread>
#include <unordered_set>
#include <vector>

#include <boost/filesystem.hpp>

#include "arrays.hpp"
#include "checks.hpp"
#include "config.hpp"
#include "../glue/arrange_glue.hpp"
#include "../glue/print_glue.hpp"
#include "errors.hpp"
#include "issues.hpp"
#include "moves.hpp"
#include "paint.hpp"
#include "result.hpp"
#include "stats.hpp"
#include "thumbnails.hpp"
#include "runtime.hpp"

#include "libslic3r/GCode/GCodeProcessor.hpp"
#include "libslic3r/LocalesUtils.hpp"
#include "libslic3r/Model.hpp"
#include "libslic3r/Print.hpp"
#include "libslic3r/PrintConfig.hpp"
#include "libslic3r/TriangleMesh.hpp"
#include "libslic3r/Utils.hpp"

namespace slicewright {

using namespace Slic3r;
namespace fs = boost::filesystem;

namespace {

using VertexArray   = nb::ndarray<const float, nb::shape<-1, 3>, nb::c_contig, nb::device::cpu>;
using TriangleArray = nb::ndarray<const int32_t, nb::shape<-1, 3>, nb::c_contig, nb::device::cpu>;

enum class State { Idle, Validating, Running, Cancelling, Done, Failed, Cancelled };

const char *state_name(State s)
{
    switch (s) {
    case State::Idle: return "idle";
    case State::Validating: return "validating";
    case State::Running: return "running";
    case State::Cancelling: return "cancelling";
    case State::Done: return "done";
    case State::Failed: return "failed";
    default: return "cancelled";
    }
}

bool terminal(State s) { return s == State::Done || s == State::Failed || s == State::Cancelled; }

// "One job per process" (04 section 9): held from start() to a terminal state.
std::atomic<bool> g_engine_busy{false};

// What the engine thread stored when a job did not finish normally; turned into the Python exception by result().
struct Failure {
    enum Kind { None, Cancelled, Validation, Slice, Config, Memory, Engine } kind = None;
    std::string message, detail, object_name, opt_key;
    std::vector<Issue> issues;
};

// Orca sets locale "C" on the TBB workers only once per process, in the first arena it runs (Thread.cpp:212), so
// workers that join later would format numbers with the process locale (04 section 9, rule 7). Every thread that
// participates in a job's arena gets the C locale for as long as it is in it: a tbb::task_scheduler_observer bound to
// the arena is told when a thread enters (before it runs any task of the arena) and leaves it. Nothing waits for
// anything: TBB's concurrency is a maximum, not a guarantee (workers are a shared pool and may be busy in another
// arena), so a barrier that needs all n threads at once can wait forever (an earlier version did, intermittently).
class CLocaleObserver : public tbb::task_scheduler_observer {
public:
    explicit CLocaleObserver(tbb::task_arena &arena) : tbb::task_scheduler_observer(arena)
    {
        observe(true);
    }
    ~CLocaleObserver() override
    {
        observe(false);
    }
    void on_scheduler_entry(bool) override
    {
#ifdef _WIN32
        _configthreadlocale(_ENABLE_PER_THREAD_LOCALE);
        std::setlocale(LC_ALL, "C");
#else
        // One handle for the whole process, never freed: a worker may still be using it after this observer is gone (it
        // is not told about threads that leave late), and freeing it under them corrupted the heap.
        static const locale_t c_locale = newlocale(LC_ALL_MASK, "C", nullptr);
        t_previous = uselocale(c_locale);
#endif
    }
    void on_scheduler_exit(bool) override
    {
#ifndef _WIN32
        // Back to the process locale only if that is what the thread had; a locale object it had set itself may be
        // gone by now, so a thread that had one keeps the C locale (never wrong for the engine).
        if (t_previous == LC_GLOBAL_LOCALE)
            uselocale(LC_GLOBAL_LOCALE);
        t_previous = nullptr;
#endif
    }

private:
#ifndef _WIN32
    static thread_local locale_t t_previous;
#endif
};
#ifndef _WIN32
thread_local locale_t CLocaleObserver::t_previous = nullptr;
#endif

// Runs `fn` inside an arena of `threads` slots whose participants all use the C locale.
template <typename F> void run_in_job_arena(int threads, F &&fn)
{
    tbb::task_arena arena(threads);
    CLocaleObserver observer(arena);  // destroyed (observe(false)) before the arena
    arena.execute(std::forward<F>(fn));
}

class SliceJob {
public:
    SliceJob() = default;
    SliceJob(const SliceJob &) = delete;
    ~SliceJob()
    {
        // Dropping a live job cancels it and joins the engine thread with the GIL released (04 section 4.4).
        nb::gil_scoped_release release;
        cancel();
        join();
    }

    void set_config(nb::handle flat);
    void set_threads(int n)
    {
        require_idle("set_threads");
        m_threads = n;
    }
    size_t add_object(const std::string &name, nb::handle vertices, nb::handle triangles, int extruder,
                      nb::handle config_overrides, nb::handle face_extruder, nb::handle face_support, nb::handle face_seam, bool repair,
                      bool ensure_on_bed);
    void set_thumbnails(nb::handle images)
    {
        require_idle("set_thumbnails");
        m_thumbnails = parse_thumbnails(images);  // raises before anything is stored
        m_validated = false;                      // the thumbnail checks are part of validate()
        m_validate_issues.clear();
    }
    nb::list validate();
    size_t validation_runs() const { return m_validation_runs; }  // private test hook: the validate() cache
    nb::list arrange(std::optional<double> spacing_mm, bool allow_rotation);
    nb::object wipe_tower_estimate()  // private test hook: the footprint arrange reserves for the tower
    {
        double x, y, w, d;
        if (!m_config_set || !glue::estimate_wipe_tower(m_model, m_config, x, y, w, d))
            return nb::none();
        return nb::make_tuple(x, y, w, d);
    }
    void start();
    nb::tuple poll();
    void cancel();
    std::shared_ptr<SliceResult> result(std::optional<double> timeout);
    std::shared_ptr<SliceResult> run(nb::handle progress, nb::handle cancel_token);

private:
    void require_idle(const char *what)
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_state != State::Idle)
            raise(errors().StateError, std::string(what) + " is not allowed in state '" + state_name(m_state) + "'", {{"state", nb::str(state_name(m_state))}});
    }
    void thread_main();
    void join()
    {
        if (m_thread.joinable())
            m_thread.join();
    }
    std::vector<Issue> apply_and_validate();
    std::vector<Issue> collect_result_issues(const GCodeProcessorResult &gcode) const;
    std::string name_of(const ObjectBase *obj) const;
    std::string name_of_id(size_t id) const;
    void finish(State s);

    std::mutex              m_mutex;
    std::condition_variable m_cv;
    State                   m_state = State::Idle;
    double                  m_percent = 0.0;
    std::string             m_message;

    bool                       m_config_set = false;
    int                        m_threads = 0;
    int                        m_effective_threads = 0;
    DynamicPrintConfig         m_config;
    Model                      m_model;
    std::vector<std::string>   m_object_names;
    std::vector<std::vector<Issue>> m_object_issues;  // per object: mesh_open_edges, moved_to_bed (from add_object)
    std::vector<uint8_t>       m_max_face_extruder;   // per object: the largest painted filament (paint_out_of_range)
    std::unique_ptr<Print>     m_print;
    std::thread                m_thread;
    bool                       m_holds_engine = false;
    Failure                    m_failure;
    std::shared_ptr<SliceResult> m_result;
    std::vector<Issue>         m_warnings;
    ThumbnailSet               m_thumbnails;
    DynamicPrintConfig         m_print_config;   // m_config after the glue: what Print::apply got
    std::vector<Issue>         m_step_warnings;  // per-step print warnings, written by the status callback
    bool                       m_validated = false;  // m_print holds the applied, validated plate
    size_t                     m_validation_runs = 0;  // how often apply_and_validate ran (test hook)
    std::vector<Issue>         m_validate_issues;
};

// ---- building ------------------------------------------------------------------------------------------

void SliceJob::set_config(nb::handle flat)
{
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_state != State::Idle || m_config_set || !m_model.objects.empty())
            raise(errors().StateError, "set_config must be called exactly once, first, on an idle job", {{"state", nb::str(state_name(m_state))}});
    }
    if (!nb::isinstance<nb::dict>(flat))
        throw nb::type_error("config must be a dict[str, str]");
    CNumericLocalesSetter locales;
    // "Enable" rather than "EnableSilent": the substitutions must be recorded to be reported (config_substitution).
    ConfigSubstitutionContext ctx(ForwardCompatibilitySubstitutionRule::Enable);
    // Load into an EMPTY config and apply onto the defaults (02 section 5.1): deserialising into
    // full_print_config() dereferences a null enum keys_map.
    DynamicPrintConfig layer;
    for (auto kv : nb::borrow<nb::dict>(flat)) {
        if (!nb::isinstance<nb::str>(kv.first) || !nb::isinstance<nb::str>(kv.second))
            throw nb::type_error("config must be a dict[str, str]");
        const std::string key = nb::cast<std::string>(kv.first), value = nb::cast<std::string>(kv.second);
        std::string k = key, v = value;
        PrintConfigDef::handle_legacy(k, v);
        if (k.empty() && print_config_def.has(key))
            continue;  // obsolete key
        // Orca's deserialiser silently ignores keys it does not know when it substitutes; 04 section 4.1 wants a
        // ConfigError for them (normalize_config is where unknown keys are dropped and reported).
        if (!print_config_def.has(key) && (k.empty() || !print_config_def.has(k)))
            raise_config_error("unknown configuration key '" + key + "'", key, value);
        try {
            layer.set_deserialize(key, value, ctx);
        } catch (const UnknownOptionException &) {
            raise_config_error("unknown configuration key '" + key + "'", key, value);
        } catch (const BadOptionValueException &e) {
            raise_config_error(e.what(), key, value);
        } catch (const ConfigurationError &e) {
            raise_config_error(e.what(), key, value);
        }
    }
    DynamicPrintConfig cfg = DynamicPrintConfig::full_print_config();
    apply_layer(cfg, layer);
    cfg.handle_legacy_composite();
    cfg.normalize_fdm();
    std::lock_guard<std::mutex> lock(m_mutex);
    m_config = std::move(cfg);
    m_config_set = true;
    for (const ConfigSubstitution &s : ctx.substitutions)
        m_warnings.push_back({"info", "config_substitution",
                              "'" + s.old_value + "' was replaced by '" + (s.new_value ? s.new_value->serialize() : "") + "'",
                              s.opt_def->opt_key, ""});
}

size_t SliceJob::add_object(const std::string &name, nb::handle vertices_h, nb::handle triangles_h, int extruder, nb::handle config_overrides,
                            nb::handle face_extruder, nb::handle face_support, nb::handle face_seam, bool repair, bool ensure_on_bed)
{
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_state != State::Idle || !m_config_set)
            raise(errors().StateError, "add_object needs an idle job with set_config called", {{"state", nb::str(state_name(m_state))}});
    }
    if (extruder < 0 || extruder > 16)
        throw nb::value_error("extruder must be in 0..16");

    // Everything that can raise happens before the model is touched, so a rejected call leaves the job as it was.
    const AnyArray varr = as_array(vertices_h, "vertices"), tarr = as_array(triangles_h, "triangles");
    const float *vertices = checked<float>(varr, "vertices", "float32", 3);
    const int32_t *triangles = checked<int32_t>(tarr, "triangles", "int32", 3);
    const size_t nv = varr.shape(0), nt = tarr.shape(0);
    if (nv == 0 || nt == 0)
        throw nb::value_error("an object needs at least one vertex and one triangle");

    // Per-face arrays (04 section 2.4): uint8, one entry per triangle, values within the paint range. Copied, so
    // the caller may free its arrays at once.
    auto face_array = [&](nb::handle h, const char *what, unsigned max) {
        std::vector<uint8_t> out;
        if (h.is_none())
            return out;
        const AnyArray a = as_array(h, what);
        const uint8_t *p = checked<uint8_t>(a, what, "uint8", 0);
        if (size_t(a.shape(0)) != nt)
            throw nb::value_error((std::string(what) + " must have one entry per triangle").c_str());
        out.assign(p, p + nt);
        for (uint8_t v : out)
            if (v > max)
                throw nb::value_error((std::string(what) + " has a value above " + std::to_string(max)).c_str());
        return out;
    };
    std::vector<uint8_t> fe = face_array(face_extruder, "face_extruder", 16), fs = face_array(face_support, "face_support", 2),
                         fm = face_array(face_seam, "face_seam", 2);

    indexed_triangle_set its;
    its.vertices.reserve(nv);
    for (size_t i = 0; i < nv; ++i) {
        const float x = vertices[3 * i], y = vertices[3 * i + 1], z = vertices[3 * i + 2];
        if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z))
            throw nb::value_error("vertices must be finite");
        its.vertices.emplace_back(x, y, z);
    }
    its.indices.reserve(nt);
    for (size_t i = 0; i < nt; ++i) {
        const int32_t a = triangles[3 * i], b = triangles[3 * i + 1], c = triangles[3 * i + 2];
        if (a < 0 || b < 0 || c < 0 || size_t(a) >= nv || size_t(b) >= nv || size_t(c) >= nv)
            throw nb::value_error("triangle index out of range");
        its.indices.emplace_back(a, b, c);
    }

    // Overrides are parsed into a scratch config first (scope and values checked) and applied to the object after.
    DynamicPrintConfig overrides;
    if (!config_overrides.is_none()) {
        if (!nb::isinstance<nb::dict>(config_overrides))
            throw nb::type_error("config_overrides must be a dict[str, str]");
        static const std::unordered_set<std::string> allowed = [] {
            std::unordered_set<std::string> s;
            for (const auto &k : PrintObjectConfig::defaults().keys()) s.insert(k);
            for (const auto &k : PrintRegionConfig::defaults().keys()) s.insert(k);
            return s;
        }();
        ConfigSubstitutionContext ctx(ForwardCompatibilitySubstitutionRule::EnableSilent);
        for (auto kv : nb::borrow<nb::dict>(config_overrides)) {
            if (!nb::isinstance<nb::str>(kv.first) || !nb::isinstance<nb::str>(kv.second))
                throw nb::type_error("config_overrides must be a dict[str, str]");
            const std::string key = nb::cast<std::string>(kv.first), value = nb::cast<std::string>(kv.second);
            if (!allowed.count(key))
                raise_config_error("'" + key + "' is not a per-object or per-region option", key, value);
            try {
                overrides.set_deserialize(key, value, ctx);
            } catch (const ConfigurationError &e) {
                raise_config_error(e.what(), key, value);
            }
        }
    }

    std::vector<Issue> object_issues;
    if (repair) {
        // repair=True only merges coincident vertices (so the face count and order never change) and reports what
        // is still open afterwards; Orca slices an open mesh but the result may have holes.
        its_merge_vertices(its, true);
        if (const size_t open = its_num_open_edges(its))
            object_issues.push_back({"warning", "mesh_open_edges",
                                     "Object '" + name + "' has " + std::to_string(open) + " open edges; the sliced result may have holes.", "", name});
    }

    ModelObject *o = m_model.add_object();
    o->name = name;
    o->add_volume(TriangleMesh(std::move(its)));  // recentres the mesh and keeps the offset in the volume
    ModelInstance *instance = o->add_instance();
    // Label id i + 1 for object i: Orca writes it into the G-code's object labels and moves.object_id maps it back.
    // (Without it the label is the instance's ObjectID, a 64-bit counter that does not fit MoveVertex's int.)
    instance->use_loaded_id_for_label = true;
    instance->loaded_id = m_model.objects.size();  // this object's index + 1: it is already in the model
    apply_paint(*o->volumes.front(), fs, fm, fe);
    // The volume offset moves to the instance: shift is minus the old centre, the instance sits at the centre (02 section 5.2).
    const Vec3d centre = o->full_raw_mesh_bounding_box().center();
    o->center_around_origin();
    o->instances.front()->set_offset(centre);
    // The object's default filament is on the OBJECT config (not the volume): 0 inherits, 1..16 is a slot.
    if (extruder > 0)
        o->config.set("extruder", extruder);
    o->config.apply(overrides, true);
    if (ensure_on_bed) {
        const double before = o->instance_bounding_box(0).min.z();
        o->ensure_on_bed();
        const double moved = o->instance_bounding_box(0).min.z() - before;
        if (std::abs(moved) > 1e-6)
            object_issues.push_back({"info", "moved_to_bed",
                                     "Object '" + name + "' was moved by " + std::to_string(moved) + " mm in Z to sit on the bed.", "", name});
    }
    m_object_names.push_back(name);
    m_object_issues.push_back(std::move(object_issues));
    m_max_face_extruder.push_back(fe.empty() ? 0 : *std::max_element(fe.begin(), fe.end()));
    m_validated = false;  // the cached validate() result no longer describes the plate
    m_validate_issues.clear();
    return m_model.objects.size() - 1;
}

// ---- validate and run ----------------------------------------------------------------------------------

// What the finished slice adds to the issues (04 section 5.5): the G-code processor's own warnings, a toolpath
// conflict between objects, and a path in an unprintable area.
std::vector<Issue> SliceJob::collect_result_issues(const GCodeProcessorResult &gcode) const
{
    std::vector<Issue> out;
    for (const GCodeProcessorResult::SliceWarning &w : gcode.warnings) {
        std::string msg = w.msg;
        for (const std::string &p : w.params)
            msg += " " + p;
        out.push_back({w.level >= 2 ? "error" : (w.level == 1 ? "warning" : "info"), "gcode_processor", msg, "", ""});
    }
    if (const auto conflict = m_print->get_conflict_result())
        out.push_back({"warning", "gcode_conflict",
                       "Toolpaths of '" + conflict->_objName1 + "' and '" + conflict->_objName2 + "' collide at Z " +
                           std::to_string(conflict->_height) + " mm.",
                       "", conflict->_objName1});
    if (gcode.gcode_check_result.error_code != 0)
        out.push_back({"warning", "engine",
                       "G-code moves into an area the printer cannot reach (check code " + std::to_string(gcode.gcode_check_result.error_code) + ").",
                       "printable_area", ""});
    return out;
}

// The add_object name of the model object `mo` ("" when it is not one of ours). Matched by id: the Print works on its own
// copy of the model (Print::apply), and a copy keeps the ObjectID of the original.
static std::string name_of_model_object(const Model &model, const std::vector<std::string> &names, const ModelObject *mo)
{
    if (mo)
        for (size_t i = 0; i < model.objects.size() && i < names.size(); ++i)
            if (model.objects[i]->id() == mo->id())
                return names[i];
    return {};
}

// Orca names objects in several ways: StringObjectException::object is a PrintObject* (most Print::validate checks), a
// ModelInstance* (the by-object clearance check, Print.cpp:678) or a ModelObject* (Print.cpp:916); none of them is
// necessarily the ModelObject whose id() the first object shares, so each is resolved to its ModelObject.
std::string SliceJob::name_of(const ObjectBase *obj) const
{
    if (!obj)
        return {};
    const ModelObject *mo = nullptr;
    if (const auto *po = dynamic_cast<const PrintObject *>(obj))
        mo = po->model_object();
    else if (const auto *mi = dynamic_cast<const ModelInstance *>(obj))
        mo = mi->get_object();
    else
        mo = dynamic_cast<const ModelObject *>(obj);
    return name_of_model_object(m_model, m_object_names, mo);
}

// SlicingError::objectId() and PrintStateBase's warning_object_id are PrintObject ids (GCode.cpp throws
// SlicingError(..., object.id().id)); the model objects' ids are a different counter.
std::string SliceJob::name_of_id(size_t id) const
{
    if (m_print)
        for (const PrintObject *po : m_print->objects())
            if (po->id().id == id)
                return name_of_model_object(m_model, m_object_names, po->model_object());
    for (size_t i = 0; i < m_model.objects.size() && i < m_object_names.size(); ++i)
        if (m_model.objects[i]->id().id == id)
            return m_object_names[i];
    return {};
}

// The engine's own checks come first and, if one of them is an error, stop the run: Orca would report the same
// plate in its own words ("nothing to be sliced") and the add-on wants one clear issue per cause (04 section 4.2).
std::vector<Issue> SliceJob::apply_and_validate()
{
    ++m_validation_runs;
    std::vector<Issue> issues = check_paint_range(m_object_names, m_max_face_extruder, m_config);
    append_unique(issues, check_bed_and_height(m_model, m_object_names, m_config));
    if (has_error(issues))
        return issues;
    for (const auto &per_object : m_object_issues)
        issues.insert(issues.end(), per_object.begin(), per_object.end());
    CNumericLocalesSetter locales;
    m_print->set_plate_origin(Vec3d::Zero());
    for (ModelObject *mo : m_model.objects)
        m_print->auto_assign_extruders(mo);
    // The CLI/GUI glue (02 section 5.9) works on a copy, so m_config stays exactly what set_config built.
    m_print_config = m_config;
    glue::prepare_print_config(m_print_config, *m_print);
    m_print->apply(m_model, m_print_config);
    m_print->is_BBL_printer() = glue::is_bbl_vendor_preset(m_print_config);  // before validate: it depends on it
    m_print->set_check_multi_filaments_compatibility(true);                  // the CLI's default (--allow-mix-temp off)
    append_unique(issues, check_thumbnails(m_thumbnails, m_print_config, m_print->is_BBL_printer()));
    // PrintObject i gets id i: it is the N of "; printing object NAME id:N copy K", which (patch 0014) the G-code
    // processor turns into moves.object_id. (The first object keeps 0, the default, so single-object G-code is
    // unchanged.)
    for (PrintObject *po : m_print->objects())
        for (size_t i = 0; i < m_model.objects.size(); ++i)
            if (m_model.objects[i]->id() == po->model_object()->id())
                po->set_id(i);
    StringObjectException warning;
    StringObjectException err = m_print->validate(&warning);
    if (!warning.string.empty())
        issues.push_back({"warning", "validation", warning.string, warning.opt_key, name_of(warning.object)});
    if (!err.string.empty())
        issues.push_back({"error", "validation", err.string, err.opt_key, name_of(err.object)});
    return issues;
}

nb::list SliceJob::validate()
{
    require_idle("validate");
    std::vector<Issue> issues;
    {
        nb::gil_scoped_release release;
        std::lock_guard<std::mutex> lock(m_mutex);  // the job is idle: nothing else touches the model
        if (!m_validated) {
            m_print = std::make_unique<Print>();
            m_validate_issues = apply_and_validate();
            m_validated = true;  // a later start() with no intervening change skips the work (04 section 4.2)
        }
        issues = m_validate_issues;
    }
    return issue_list(issues);
}

// 04 section 4.5: synchronous, GIL released, holds the engine lock (Busy while a slice runs), idle jobs only. The
// job's objects are not changed: the add-on moves its Blender objects and builds a new job.
nb::list SliceJob::arrange(std::optional<double> spacing_mm, bool allow_rotation)
{
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_state != State::Idle || !m_config_set)
            raise(errors().StateError, "arrange needs an idle job with set_config called", {{"state", nb::str(state_name(m_state))}});
    }
    if (spacing_mm && (!std::isfinite(*spacing_mm) || *spacing_mm < 0))
        throw nb::value_error("spacing_mm must be >= 0");
    bool expected = false;
    if (!g_engine_busy.compare_exchange_strong(expected, true))
        raise(errors().Busy, "another job or arrange call is running");
    struct Unlock {
        ~Unlock() { g_engine_busy.store(false); }
    } unlock;

    std::vector<glue::Placed> placed;
    std::string error;
    if (!m_model.objects.empty()) {
        nb::gil_scoped_release release;
        try {
            CNumericLocalesSetter locales;
            const int available = std::max(1, tbb::info::default_concurrency());
            const int threads = std::max(1, std::min(m_threads > 0 ? m_threads : std::max(1, available - 1), available));
            run_in_job_arena(threads, [&] { placed = glue::arrange_model(m_model, m_config, spacing_mm ? *spacing_mm : -1.0, allow_rotation); });
        } catch (const std::exception &e) {
            error = e.what();
        }
    }
    if (!error.empty())
        raise(errors().ArrangeError, "arrange failed: " + error, {{"object_names", nb::list()}});

    nb::list unfit;
    for (const glue::Placed &p : placed)
        if (!p.fitted)
            unfit.append(nb::str(m_object_names[p.index].c_str()));
    if (nb::len(unfit) > 0)
        raise(errors().ArrangeError, std::to_string(nb::len(unfit)) + " object(s) do not fit on the bed (printable_area minus bed_exclude_area, wipe tower)",
              {{"object_names", unfit}});

    nb::list out;
    for (const glue::Placed &p : placed) {
        // new_vertex = T(new) * Rz(rotation) * T(-old): rotate about the instance's own centre, then move it.
        const double c = std::cos(p.rotation), s = std::sin(p.rotation);
        auto *m = new double[16]{c, -s, 0, p.new_x - (c * p.old_x - s * p.old_y),
                                 s, c, 0, p.new_y - (s * p.old_x + c * p.old_y),
                                 0, 0, 1, 0,
                                 0, 0, 0, 1};
        nb::capsule owner(m, [](void *q) noexcept { delete[] static_cast<double *>(q); });
        const size_t shape[2] = {4, 4};
        nb::ndarray<nb::numpy, double> transform(m, 2, shape, owner);
        nb::dict d;
        d["index"] = p.index;
        d["name"] = nb::str(m_object_names[p.index].c_str());
        d["transform"] = nb::cast(transform);
        d["translation"] = nb::make_tuple(m[3], m[7], m[11]);
        d["rotation_z"] = p.rotation;
        out.append(d);
    }
    return out;
}

void SliceJob::start()
{
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_state != State::Idle || !m_config_set)
            raise(errors().StateError, "start needs an idle, configured job", {{"state", nb::str(state_name(m_state))}});
        if (m_model.objects.empty())
            raise(errors().StateError, "the job has no objects", {{"state", nb::str(state_name(m_state))}});
    }
    bool expected = false;
    if (!g_engine_busy.compare_exchange_strong(expected, true))
        raise(errors().Busy, "another job or arrange call is running");
    std::lock_guard<std::mutex> lock(m_mutex);
    m_holds_engine = true;
    m_state = State::Validating;
    m_percent = 0;
    m_message.clear();
    if (!m_validated)
        m_print = std::make_unique<Print>();  // else the Print validate() applied is reused, so start() skips that work
    m_print->set_status_callback([this](const PrintBase::SlicingStatus &s) {
        std::lock_guard<std::mutex> lk(m_mutex);  // fires from TBB workers: only writes the slot
        if (s.warning_step != -1) {
            // A per-step print warning (04 section 5.5): not progress, and not a message to show as the stage.
            if (!s.text.empty() && s.message_type != PrintStateBase::SlicingDefaultNotification)
                append_unique(m_step_warnings, {{s.warning_level == PrintStateBase::WarningLevel::CRITICAL ? "warning" : "info", "slicing",
                                                 s.text, "", name_of_id(s.warning_object_id.id)}});
            return;
        }
        if (s.percent >= 0 && s.percent > m_percent)
            m_percent = std::min(99.0, double(s.percent));
        if (!s.text.empty())
            m_message = s.text;
    });
    try {
        m_thread = std::thread([this] { thread_main(); });
    } catch (const std::system_error &e) {
        // Nothing ran: the job stays idle and the engine lock is free again.
        m_holds_engine = false;
        m_state = State::Idle;
        m_print.reset();
        g_engine_busy.store(false);
        raise(errors().EngineError, std::string("cannot start the engine thread: ") + e.what(), {{"detail", nb::str("std::system_error")}});
    }
}

void SliceJob::finish(State s)
{
    // The engine lock goes first: a caller that sees a terminal state through poll() may start the next job at
    // once (04 section 8: held "until the job reaches a terminal state"), and must not get Busy.
    if (m_holds_engine) {
        m_holds_engine = false;
        g_engine_busy.store(false);
    }
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_state = s;
        if (s == State::Done)
            m_percent = 100.0;
    }
    m_cv.notify_all();
}

void SliceJob::thread_main()
{
    // Nothing here touches the CPython API (04 section 9 rule 2); every exception is caught and stored.
    State final_state = State::Failed;
    try {
        CNumericLocalesSetter locales;
        // Orca's name_tbb_thread_pool_threads_set_locale() (first Print::process of the process) runs a barrier of
        // this_task_arena::max_concurrency() tasks (Thread.cpp:222) and waits until all of them run at the same time,
        // so an arena that is larger than the number of threads TBB can actually supply never finishes (seen on a
        // 3-core CI runner with set_threads(8)). The arena is therefore clamped to tbb::info::default_concurrency(),
        // which, unlike std::thread::hardware_concurrency(), respects affinity masks and cgroup CPU limits.
        const int available = std::max(1, tbb::info::default_concurrency());
        const int requested = m_threads > 0 ? m_threads : std::max(1, available - 1);
        const int threads = std::max(1, std::min(requested, available));
        m_effective_threads = threads;
        // The job runs inside an arena of `threads` slots (the engine thread is one of them). Capping with a
        // tbb::global_control on this thread hung the first parallel_for with the statically linked oneTBB 2021.5 in
        // M2. The likely cause is the same barrier: it sizes itself by the arena's concurrency, which a global_control
        // lowers underneath it, so the arena (whose concurrency is the cap) is used instead.
        run_in_job_arena(threads, [&] {
        // Cached when validate() already ran on this plate (m_print then holds it, applied).
        std::vector<Issue> issues = m_validated ? m_validate_issues : apply_and_validate();
        const bool has_error = slicewright::has_error(issues);
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            append_unique(m_warnings, issues);
            if (has_error) {
                m_failure.kind = Failure::Validation;
                m_failure.message = "validation failed";
                m_failure.issues = issues;
                for (const Issue &i : issues)
                    if (i.level == "error") {
                        m_failure.opt_key = i.opt_key;
                        m_failure.object_name = i.object_name;
                        m_failure.message = i.message;
                        break;
                    }
            } else if (m_state == State::Cancelling) {
                throw CanceledException();
            } else {
                m_state = State::Running;
            }
        }
        if (!has_error) {
            // Process-global state, set per job under the engine lock (04 section 9 rule 3): the G-code dialect flag
            // (it defaults to true and the wipe-tower constructors flip it) and the tables Brim.cpp:138 and the speed
            // code read (OrcaSlicer.cpp:6138-6139).
            GCodeProcessor::s_IsBBLPrinter = m_print->is_BBL_printer();
            glue::set_global_tables(m_print_config, *m_print);
            m_print->process();
            // G-code export has few cancellation points of its own, so honour a cancel that arrived in the
            // last step of process() here, before the long and mostly uncancellable tail starts.
            if (m_print->canceled())
                throw CanceledException();
            // A name no other job or result can share: CPython reuses freed addresses, so `this` is not unique, and a
            // result deletes its file when it is freed (04 section 5.1).
            const fs::path out = fs::path(temporary_dir()) / fs::unique_path("job-%%%%%%%%-%%%%%%%%-%%%%%%%%.gcode");
            GCodeProcessorResult gcode;
            std::string path = m_print->export_gcode(out.string(), &gcode, thumbnail_callback(m_thumbnails));
            auto res = std::make_shared<SliceResult>();
            res->gcode_path = path;
            res->objects = m_object_names;
            {
                std::lock_guard<std::mutex> lock(m_mutex);
                m_message = "Converting moves";  // percent stays below 100 until the result exists
            }
            convert_moves(gcode, m_print->get_filament_maps(), m_object_names.size(), *res->store);
            res->stats = compute_stats(gcode, *m_print, *res->store);
            res->stats.threads = m_effective_threads;
            {
                std::lock_guard<std::mutex> lock(m_mutex);
                res->warnings = m_warnings;
                append_unique(res->warnings, m_step_warnings);
            }
            append_unique(res->warnings, collect_result_issues(gcode));
            {
                std::lock_guard<std::mutex> lock(m_mutex);
                m_result = res;
            }
            final_state = State::Done;
        }
        });
    } catch (const CanceledException &) {
        m_failure.kind = Failure::Cancelled;
        final_state = State::Cancelled;
    } catch (const SlicingError &e) {
        m_failure = {Failure::Slice, e.what(), "", name_of_id(e.objectId()), "", {}};
    } catch (const SlicingErrors &e) {
        const std::string name = e.errors_.empty() ? std::string() : name_of_id(e.errors_.front().objectId());
        m_failure = {Failure::Slice, e.errors_.empty() ? std::string(e.what()) : std::string(e.errors_.front().what()), "", name, "", {}};
    } catch (const UnknownOptionException &e) {
        m_failure = {Failure::Config, e.what(), "", "", "", {}};
    } catch (const ConfigurationError &e) {
        m_failure = {Failure::Config, e.what(), "", "", "", {}};
    } catch (const PlaceholderParserError &e) {
        // A custom G-code template that fails to evaluate (GCode::check_placeholder_parser_failed): a config problem, as it is
        // on the calling thread (04 section 7).
        m_failure = {Failure::Config, e.what(), "", "", "", {}};
    } catch (const std::bad_alloc &) {
        m_failure = {Failure::Memory, "out of memory", "", "", "", {}};
    } catch (const std::exception &e) {
        m_failure = {Failure::Engine, e.what(), typeid(e).name(), "", "", {}};
    } catch (...) {
        m_failure = {Failure::Engine, "unknown native exception", "unknown", "", "", {}};
    }
    finish(final_state);
}

nb::tuple SliceJob::poll()
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_state == State::Idle)
        return nb::make_tuple("idle", 0.0, "");
    return nb::make_tuple(state_name(m_state), m_state == State::Done ? 100.0 : m_percent, m_state == State::Done ? "" : m_message.c_str());
}

void SliceJob::cancel()
{
    Print *print = nullptr;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_state != State::Validating && m_state != State::Running)
            return;
        m_state = State::Cancelling;
        print = m_print.get();
    }
    // Outside the state mutex: Orca's status callback (a TBB worker) takes it, and Print::cancel() is one atomic
    // store that the steps poll through throw_if_canceled(), so there is no lock order to get wrong.
    if (print)
        print->cancel();
}

std::shared_ptr<SliceResult> SliceJob::result(std::optional<double> timeout)
{
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_state == State::Idle)
            raise(errors().StateError, "result() on a job that was not started", {{"state", nb::str("idle")}});
    }
    if (timeout && std::isnan(*timeout))
        throw nb::value_error("timeout must not be NaN");
    bool timed_out = false;
    {
        nb::gil_scoped_release release;
        std::unique_lock<std::mutex> lock(m_mutex);
        auto done = [&] { return terminal(m_state); };
        // No timeout, an infinite one or one too large for the clock's range (std::chrono would overflow) waits without a limit.
        if (timeout && std::isfinite(*timeout) && *timeout < 1e9) {
            timed_out = !m_cv.wait_for(lock, std::chrono::duration<double>(*timeout), done);
        } else {
            m_cv.wait(lock, done);
        }
    }
    if (timed_out)
        {
            PyErr_SetString(PyExc_TimeoutError, "the job did not finish in time");
            throw nb::python_error();
        }
    join();
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_state == State::Done)
        return m_result;
    const Failure &f = m_failure;
    switch (f.kind) {
    case Failure::Cancelled: raise(errors().Cancelled, "cancelled");
    case Failure::Validation: {
        nb::list issues;
        for (const Issue &i : f.issues)
            issues.append(issue_dict(i));
        raise(errors().ValidationError, f.message,
              {{"opt_key", f.opt_key.empty() ? nb::object(nb::none()) : nb::object(nb::str(f.opt_key.c_str()))},
               {"object_name", f.object_name.empty() ? nb::object(nb::none()) : nb::object(nb::str(f.object_name.c_str()))},
               {"issues", issues}});
    }
    case Failure::Slice:
        raise(errors().SliceError, f.message,
              {{"object_name", f.object_name.empty() ? nb::object(nb::none()) : nb::object(nb::str(f.object_name.c_str()))}});
    case Failure::Config: raise_config_error(f.message);
    case Failure::Memory: PyErr_NoMemory(); throw nb::python_error();
    default: raise(errors().EngineError, f.message, {{"detail", nb::str(f.detail.c_str())}});
    }
}

std::shared_ptr<SliceResult> SliceJob::run(nb::handle progress, nb::handle cancel_token)
{
    start();
    for (;;) {
        bool finished = false;
        {
            nb::gil_scoped_release release;
            std::unique_lock<std::mutex> lock(m_mutex);
            finished = m_cv.wait_for(lock, std::chrono::milliseconds(50), [&] { return terminal(m_state); });
        }
        nb::tuple p = poll();
        try {
            if (!progress.is_none())
                progress(p[1], p[2]);
            if (!cancel_token.is_none() && nb::cast<bool>(cancel_token.attr("cancelled")))
                cancel();
            // Ctrl-C: Python's signal handler only runs when the interpreter asks for it, and a native loop never
            // does. A handler that raises (KeyboardInterrupt) leaves the error set; it takes the cleanup path below.
            if (PyErr_CheckSignals() != 0)
                throw nb::python_error();
        } catch (const std::exception &) {
            // Anything thrown above (a raising callback, a cancel token whose `cancelled` does not cast to bool,
            // KeyboardInterrupt from the signal check) must not leave the job running: it would keep the
            // process-wide lock (Busy for every later job) with nobody left to poll it. Cancel, wait for the
            // engine thread, then re-raise (nb::python_error is a std::exception too).
            cancel();
            {
                nb::gil_scoped_release release;
                std::unique_lock<std::mutex> lock(m_mutex);
                m_cv.wait(lock, [&] { return terminal(m_state); });
            }
            join();
            throw;
        }
        if (finished)
            break;
    }
    return result(std::nullopt);
}

} // namespace

void bind_job(nb::module_ &m)
{
    // Test hook (private, unversioned): sleeps WITHOUT releasing the GIL. The negative control of the GIL tests: a probe that
    // cannot tell this from a call that releases it proves nothing.
    m.def("_hold_gil_sleep", [](double seconds) { std::this_thread::sleep_for(std::chrono::duration<double>(seconds)); }, nb::arg("seconds"));
    // Test hook (private, unversioned): runs a parallel_for in a job arena of `threads` slots and reports the decimal
    // point each task saw (localeconv, i.e. the locale of the thread that ran it) and how many distinct threads ran tasks.
    m.def(
        "_locale_probe",
        [](int threads) {
            std::mutex mtx;
            std::set<std::string> points;
            std::set<std::thread::id> ids;
            {
                nb::gil_scoped_release release;
                run_in_job_arena(std::max(1, threads), [&] {
                    tbb::parallel_for(0, 4000, [&](int) {
                        const std::string point = localeconv()->decimal_point;
                        {
                            std::lock_guard<std::mutex> lock(mtx);
                            points.insert(point);
                            ids.insert(std::this_thread::get_id());
                        }
                        std::this_thread::sleep_for(std::chrono::microseconds(200));
                    });
                });
            }
            nb::dict out;
            nb::list l;
            for (const auto &p : points)
                l.append(nb::str(p.c_str()));
            out["decimal_points"] = l;
            out["threads_seen"] = ids.size();
            return out;
        },
        nb::arg("threads"));
    // Test hook (private, unversioned): the process-global tables the CLI/GUI glue fills per job (02 section 5.9).
    m.def("_glue_state", [] {
        nb::dict out, params;
        for (const auto &kv : Model::extruderParamsMap) {
            nb::dict e;
            e["material"] = nb::str(kv.second.materialName.c_str());
            e["bed_temp"] = kv.second.bedTemp;
            e["end_temp"] = kv.second.heatEndTemp;
            params[nb::int_(kv.first)] = e;
        }
        out["extruder_params"] = params;
        nb::dict speeds;
        speeds["perimeter"] = Model::printSpeedMap.perimeterSpeed;
        speeds["external_perimeter"] = Model::printSpeedMap.externalPerimeterSpeed;
        speeds["infill"] = Model::printSpeedMap.infillSpeed;
        speeds["max"] = Model::printSpeedMap.maxSpeed;
        speeds["bed_points"] = Model::printSpeedMap.bed_poly.points.size();
        out["speed_map"] = speeds;
        out["is_bbl_processor"] = GCodeProcessor::s_IsBBLPrinter;
        return out;
    });
    nb::class_<SliceJob>(m, "SliceJob")
        .def(nb::init<>())
        .def("set_config", &SliceJob::set_config, nb::arg("flat"))
        .def("set_threads", &SliceJob::set_threads, nb::arg("n"))
        .def("add_object", &SliceJob::add_object, nb::arg("name"), nb::arg("vertices").noconvert(), nb::arg("triangles").noconvert(),
             nb::kw_only(), nb::arg("extruder") = 0, nb::arg("config_overrides") = nb::none(), nb::arg("face_extruder") = nb::none(),
             nb::arg("face_support") = nb::none(), nb::arg("face_seam") = nb::none(), nb::arg("repair") = false,
             nb::arg("ensure_on_bed") = false)
        .def("set_thumbnails", &SliceJob::set_thumbnails, nb::arg("images"))
        .def("validate", &SliceJob::validate)
        .def("_validation_runs", &SliceJob::validation_runs)
        .def("_wipe_tower_estimate", &SliceJob::wipe_tower_estimate)
        .def("arrange", &SliceJob::arrange, nb::arg("spacing_mm") = nb::none(), nb::arg("allow_rotation") = false)
        .def("start", &SliceJob::start)
        .def("poll", &SliceJob::poll)
        .def("cancel", &SliceJob::cancel)
        .def("result", &SliceJob::result, nb::arg("timeout") = nb::none())
        .def("run", &SliceJob::run, nb::arg("progress") = nb::none(), nb::arg("cancel") = nb::none());
}

} // namespace slicewright
