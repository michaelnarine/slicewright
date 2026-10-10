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

#include <clocale>
#ifndef _WIN32
#include <locale.h>
#include <xlocale.h>
#endif

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <thread>
#include <unordered_set>
#include <vector>

#include <boost/filesystem.hpp>

#include "config.hpp"
#include "errors.hpp"
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

struct Issue {
    std::string level, code, message, opt_key, object_name;
};

nb::dict issue_dict(const Issue &i)
{
    nb::dict d;
    d["level"] = nb::str(i.level.c_str());
    d["code"] = nb::str(i.code.c_str());
    d["message"] = nb::str(i.message.c_str());
    d["opt_key"] = i.opt_key.empty() ? nb::object(nb::none()) : nb::object(nb::str(i.opt_key.c_str()));
    d["object_name"] = i.object_name.empty() ? nb::object(nb::none()) : nb::object(nb::str(i.object_name.c_str()));
    return d;
}

// What the engine thread stored when a job did not finish normally; turned into the Python exception by result().
struct Failure {
    enum Kind { None, Cancelled, Validation, Slice, Config, Memory, Engine } kind = None;
    std::string message, detail, object_name, opt_key;
    std::vector<Issue> issues;
};

// A finished slice. Owns the G-code file in the engine's temporary directory.
class SliceResult {
public:
    std::string              gcode_path;
    std::vector<std::string> objects;
    std::vector<Issue>       warnings;
    size_t                   layer_count = 0;
    double                   time_normal = 0, time_silent = 0;
    std::string              time_display;

    ~SliceResult()
    {
        boost::system::error_code ec;
        if (!gcode_path.empty())
            fs::remove(gcode_path, ec);
    }

    int                      threads = 0;  // size of the TBB arena the job ran in

    // Runs without the GIL: touches no Python object. Returns an empty string on success, else the error text,
    // which the caller turns into an exception after it has the GIL back.
    std::string write_gcode(const std::string &path) const noexcept
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
};

// Orca sets locale "C" on the TBB workers only once per process, in the first arena it runs (Thread.cpp:212), so
// workers that join later, in a larger arena, would format numbers with the process locale (04 section 9, rule 7).
// This runs the same kind of barrier in every job's arena: `n` tasks that all wait for each other, so each of the
// `n` threads takes exactly one, and each worker sets "C" on itself. `n` never exceeds the threads TBB can supply
// (the caller clamps it), so the barrier cannot hang.
void set_c_locale_on_workers(int n)
{
    if (n <= 1)
        return;
    std::mutex m;
    std::condition_variable cv;
    int running = 0;
    const auto master = std::this_thread::get_id();
    tbb::parallel_for(tbb::blocked_range<int>(0, n, 1), [&](const tbb::blocked_range<int> &) {
        {
            std::unique_lock<std::mutex> lk(m);
            if (++running == n) {
                lk.unlock();
                cv.notify_all();
            } else {
                cv.wait(lk, [&] { return running == n; });
            }
        }
        if (std::this_thread::get_id() == master)
            return;
#ifdef _WIN32
        _configthreadlocale(_ENABLE_PER_THREAD_LOCALE);
        std::setlocale(LC_ALL, "C");
#else
        static const locale_t c_locale = newlocale(
#ifdef __APPLE__
            LC_ALL_MASK
#else
            LC_ALL
#endif
            , "C", nullptr);
        uselocale(c_locale);
#endif
    });
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
    size_t add_object(const std::string &name, VertexArray vertices, TriangleArray triangles, int extruder,
                      nb::handle config_overrides, nb::handle face_extruder, nb::handle face_support, nb::handle face_seam, bool repair,
                      bool ensure_on_bed);
    void set_thumbnails(nb::handle) { raise(errors().EngineError, "set_thumbnails is not implemented yet (M5)", {{"detail", nb::str("not implemented")}}); }
    nb::list validate();
    nb::list arrange(nb::handle, bool) { raise(errors().EngineError, "arrange is not implemented yet (M5)", {{"detail", nb::str("not implemented")}}); }
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
    std::unique_ptr<Print>     m_print;
    std::thread                m_thread;
    bool                       m_holds_engine = false;
    Failure                    m_failure;
    std::shared_ptr<SliceResult> m_result;
    std::vector<Issue>         m_warnings;
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
    ConfigSubstitutionContext ctx(ForwardCompatibilitySubstitutionRule::EnableSilent);
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

size_t SliceJob::add_object(const std::string &name, VertexArray vertices, TriangleArray triangles, int extruder, nb::handle config_overrides,
                            nb::handle face_extruder, nb::handle face_support, nb::handle face_seam, bool repair, bool ensure_on_bed)
{
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_state != State::Idle || !m_config_set)
            raise(errors().StateError, "add_object needs an idle job with set_config called", {{"state", nb::str(state_name(m_state))}});
    }
    if (extruder < 0 || extruder > 16)
        throw nb::value_error("extruder must be in 0..16");
    const size_t nv = vertices.shape(0), nt = triangles.shape(0);
    if (nv == 0 || nt == 0)
        throw nb::value_error("an object needs at least one vertex and one triangle");

    // Per-face paint arrives with M5: validate shape and range now, refuse non-zero data rather than ignore it.
    auto check_face = [&](nb::handle h, const char *what, unsigned max) {
        if (h.is_none())
            return;
        auto arr = nb::cast<nb::ndarray<const uint8_t, nb::shape<-1>, nb::c_contig, nb::device::cpu>>(h);
        if (arr.shape(0) != nt)
            throw nb::value_error((std::string(what) + " must have one entry per triangle").c_str());
        for (size_t i = 0; i < nt; ++i) {
            if (arr.data()[i] > max)
                throw nb::value_error((std::string(what) + " has a value above " + std::to_string(max)).c_str());
            if (arr.data()[i] != 0)
                raise(errors().EngineError, std::string("painted faces (") + what + ") are implemented in M5", {{"detail", nb::str("not implemented")}});
        }
    };
    check_face(face_extruder, "face_extruder", 16);
    check_face(face_support, "face_support", 2);
    check_face(face_seam, "face_seam", 2);
    (void) repair;  // repair=True only merges vertices (M5)

    indexed_triangle_set its;
    its.vertices.reserve(nv);
    for (size_t i = 0; i < nv; ++i)
        its.vertices.emplace_back(vertices.data()[3 * i], vertices.data()[3 * i + 1], vertices.data()[3 * i + 2]);
    its.indices.reserve(nt);
    for (size_t i = 0; i < nt; ++i) {
        const int32_t a = triangles.data()[3 * i], b = triangles.data()[3 * i + 1], c = triangles.data()[3 * i + 2];
        if (a < 0 || b < 0 || c < 0 || size_t(a) >= nv || size_t(b) >= nv || size_t(c) >= nv)
            throw nb::value_error("triangle index out of range");
        its.indices.emplace_back(a, b, c);
    }

    ModelObject *o = m_model.add_object();
    o->name = name;
    o->add_volume(TriangleMesh(std::move(its)));  // recentres the mesh and keeps the offset in the volume
    o->add_instance();
    // The volume offset moves to the instance: shift is minus the old centre, the instance sits at the centre (02 section 5.2).
    const Vec3d centre = o->full_raw_mesh_bounding_box().center();
    o->center_around_origin();
    o->instances.front()->set_offset(centre);
    if (extruder > 0)
        o->config.set("extruder", extruder);      // on the OBJECT, not the volume
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
            const std::string key = nb::cast<std::string>(kv.first), value = nb::cast<std::string>(kv.second);
            if (!allowed.count(key))
                raise_config_error("'" + key + "' is not a per-object or per-region option", key, value);
            try {
                o->config.set_deserialize(key, value, ctx);
            } catch (const ConfigurationError &e) {
                raise_config_error(e.what(), key, value);
            }
        }
    }
    if (ensure_on_bed)
        o->ensure_on_bed();
    m_object_names.push_back(name);
    return m_model.objects.size() - 1;
}

// ---- validate and run ----------------------------------------------------------------------------------

std::vector<Issue> SliceJob::apply_and_validate()
{
    std::vector<Issue> issues;
    CNumericLocalesSetter locales;
    m_print->set_plate_origin(Vec3d::Zero());
    m_print->is_BBL_printer() = false;  // dialect selection from printer_model is M5 (CLI glue, 02 section 5.9)
    if (m_config.opt_string("printer_model").compare(0, 9, "Bambu Lab") == 0)
        issues.push_back({"warning", "engine",
                          "printer_model is '" + m_config.opt_string("printer_model") +
                              "' but the Bambu G-code dialect is not selected yet (is_BBL_printer stays false until the M5 glue); "
                              "the G-code may differ from Orca's output for this printer",
                          "printer_model", ""});
    for (ModelObject *mo : m_model.objects)
        m_print->auto_assign_extruders(mo);
    m_print->apply(m_model, m_config);
    StringObjectException warning;
    StringObjectException err = m_print->validate(&warning);
    if (!warning.string.empty())
        issues.push_back({"warning", "validation", warning.string, warning.opt_key, ""});
    if (!err.string.empty())
        issues.push_back({"error", "validation", err.string, err.opt_key, ""});
    return issues;
}

nb::list SliceJob::validate()
{
    require_idle("validate");
    std::vector<Issue> issues;
    {
        nb::gil_scoped_release release;
        std::lock_guard<std::mutex> lock(m_mutex);  // the job is idle: nothing else touches the model
        m_print = std::make_unique<Print>();
        issues = apply_and_validate();
    }
    nb::list out;
    for (const Issue &i : issues)
        out.append(issue_dict(i));
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
    m_print = std::make_unique<Print>();
    m_print->set_status_callback([this](const PrintBase::SlicingStatus &s) {
        std::lock_guard<std::mutex> lk(m_mutex);  // fires from TBB workers: only writes the slot
        if (s.percent >= 0 && s.percent > m_percent)
            m_percent = std::min(99.0, double(s.percent));
        if (!s.text.empty())
            m_message = s.text;
    });
    m_thread = std::thread([this] { thread_main(); });
}

void SliceJob::finish(State s)
{
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_state = s;
        if (s == State::Done)
            m_percent = 100.0;
    }
    if (m_holds_engine) {
        m_holds_engine = false;
        g_engine_busy.store(false);
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
        tbb::task_arena arena(threads);
        arena.execute([&] {
        set_c_locale_on_workers(threads);
        // Process-global, defaults to true, and is also flipped by the wipe-tower constructors: reset it from this
        // job's config (the BBL dialect selection from printer_model arrives with the M5 glue).
        GCodeProcessor::s_IsBBLPrinter = false;
        std::vector<Issue> issues = apply_and_validate();
        bool has_error = std::any_of(issues.begin(), issues.end(), [](const Issue &i) { return i.level == "error"; });
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            m_warnings.insert(m_warnings.end(), issues.begin(), issues.end());
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
            m_print->process();
            // A name no other job or result can share: CPython reuses freed addresses, so `this` is not unique, and a
            // result deletes its file when it is freed (04 section 5.1).
            const fs::path out = fs::path(temporary_dir()) / fs::unique_path("job-%%%%%%%%-%%%%%%%%-%%%%%%%%.gcode");
            GCodeProcessorResult gcode;
            std::string path = m_print->export_gcode(out.string(), &gcode, nullptr);
            auto res = std::make_shared<SliceResult>();
            res->gcode_path = path;
            res->threads = m_effective_threads;
            res->objects = m_object_names;
            for (const PrintObject *obj : m_print->objects())
                res->layer_count += obj->layer_count();
            const auto &modes = gcode.print_statistics.modes;
            res->time_normal = modes[size_t(PrintEstimatedStatistics::ETimeMode::Normal)].time;
            res->time_silent = modes[size_t(PrintEstimatedStatistics::ETimeMode::Stealth)].time;
            res->time_display = m_print->print_statistics().estimated_normal_print_time;
            {
                std::lock_guard<std::mutex> lock(m_mutex);
                res->warnings = m_warnings;
                m_result = res;
            }
            final_state = State::Done;
        }
        });
    } catch (const CanceledException &) {
        m_failure.kind = Failure::Cancelled;
        final_state = State::Cancelled;
    } catch (const SlicingError &e) {
        m_failure = {Failure::Slice, e.what(), "", "", "", {}};
    } catch (const SlicingErrors &e) {
        m_failure = {Failure::Slice, e.what(), "", "", "", {}};
    } catch (const UnknownOptionException &e) {
        m_failure = {Failure::Config, e.what(), "", "", "", {}};
    } catch (const ConfigurationError &e) {
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
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_state == State::Validating || m_state == State::Running) {
        m_state = State::Cancelling;
        if (m_print)
            m_print->cancel();
    }
}

std::shared_ptr<SliceResult> SliceJob::result(std::optional<double> timeout)
{
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_state == State::Idle)
            raise(errors().StateError, "result() on a job that was not started", {{"state", nb::str("idle")}});
    }
    bool timed_out = false;
    {
        nb::gil_scoped_release release;
        std::unique_lock<std::mutex> lock(m_mutex);
        auto done = [&] { return terminal(m_state); };
        if (timeout) {
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
    case Failure::Slice: raise(errors().SliceError, f.message, {{"object_name", nb::none()}});
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
        } catch (const nb::python_error &) {
            // A raising callback must not leave the job running: it would keep the process-wide lock (Busy for
            // every later job) with nobody left to poll it. Cancel, wait for the engine thread, then re-raise.
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
    nb::class_<SliceResult>(m, "SliceResult")
        .def_prop_ro("gcode_path", [](const SliceResult &r) { return r.gcode_path; })
        .def_prop_ro("objects", [](const SliceResult &r) { return r.objects; })
        .def_prop_ro("wipe_tower", [](const SliceResult &) { return nb::none(); })
        .def_prop_ro("warnings",
                     [](const SliceResult &r) {
                         nb::list l;
                         for (const Issue &i : r.warnings)
                             l.append(issue_dict(i));
                         return l;
                     })
        .def_prop_ro("stats",
                     [](const SliceResult &r) {
                         // Partial until M5 (04 section 5.4): totals only.
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
        .def("write_gcode", [](const SliceResult &r, const std::string &path) {
            std::string error;
            {
                nb::gil_scoped_release release;
                error = r.write_gcode(path);
            }
            // Only now, with the GIL held again, may a Python exception object be built.
            if (!error.empty())
                raise(errors().EngineError, error, {{"detail", nb::str("filesystem")}});
        }, nb::arg("path"));

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
        .def("arrange", &SliceJob::arrange, nb::arg("spacing_mm") = nb::none(), nb::arg("allow_rotation") = false)
        .def("start", &SliceJob::start)
        .def("poll", &SliceJob::poll)
        .def("cancel", &SliceJob::cancel)
        .def("result", &SliceJob::result, nb::arg("timeout") = nb::none())
        .def("run", &SliceJob::run, nb::arg("progress") = nb::none(), nb::arg("cancel") = nb::none());
}

} // namespace slicewright
