// SPDX-License-Identifier: AGPL-3.0-only
#include "config.hpp"

#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <cfloat>
#include <cstdlib>
#include <memory>
#include <optional>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <unordered_set>
#include <vector>

#include "errors.hpp"

#include "libslic3r/Config.hpp"
#include "libslic3r/LocalesUtils.hpp"
#include "libslic3r/PlaceholderParser.hpp"
#include "libslic3r/Preset.hpp"
#include "libslic3r/PresetBundle.hpp"
#include "libslic3r/PrintConfig.hpp"

namespace slicewright {

using namespace Slic3r;

namespace {

// ---- keys that describe a preset rather than configure the slicer (04 section 2.5) -------------------------
const std::set<std::string> &metadata_keys()
{
    static const std::set<std::string> keys = {
        "name", "inherits", "include", "from", "instantiation", "setting_id", "filament_id", "description", "version",
        "type", "is_custom", "renamed_from", "url", "base_id", "user_id", "updated_time", "sync_info",
    };
    return keys;
}

// ---- Python <-> C++ conversions ----------------------------------------------------------------------------

// A PresetDict value: a string, or a list of strings (04 section 2.5).
struct Value {
    bool                     is_list = false;
    std::string              scalar;
    std::vector<std::string> items;
};

std::string to_str(nb::handle h, bool lenient, const std::string &key)
{
    if (nb::isinstance<nb::str>(h))
        return nb::cast<std::string>(h);
    if (lenient)
        return nb::cast<std::string>(nb::str(h));
    throw nb::type_error(("value of '" + key + "' must be str or list[str], got " +
                          nb::cast<std::string>(nb::type_name(h.type()))).c_str());
}

std::vector<std::pair<std::string, Value>> read_dict(nb::handle obj, bool lenient, const char *what)
{
    if (!nb::isinstance<nb::dict>(obj))
        throw nb::type_error((std::string(what) + " must be a dict").c_str());
    std::vector<std::pair<std::string, Value>> out;
    for (auto kv : nb::borrow<nb::dict>(obj)) {
        if (!nb::isinstance<nb::str>(kv.first))
            throw nb::type_error((std::string(what) + " keys must be str").c_str());
        const std::string key = nb::cast<std::string>(kv.first);
        Value v;
        if (nb::isinstance<nb::list>(kv.second) || nb::isinstance<nb::tuple>(kv.second)) {
            v.is_list = true;
            for (nb::handle item : kv.second)
                v.items.push_back(to_str(item, lenient, key));
        } else {
            v.scalar = to_str(kv.second, lenient, key);
        }
        out.emplace_back(key, std::move(v));
    }
    return out;
}

// The string Orca's JSON loader would build for an array value (ConfigBase::load_from_json): elements joined by
// ',' (numbers, points), ';' with C-style quoting for string vectors, '#' for point groups.
std::string serialize_value(const std::string &key, const Value &v)
{
    if (!v.is_list)
        return v.scalar;
    char sep = ',';
    bool quote = false;
    if (const ConfigOptionDef *def = print_config_def.get(key)) {
        if (def->type == coStrings) {
            sep = ';';
            quote = true;
        } else if (def->type == coPointsGroups) {
            sep = '#';
        }
    }
    std::string out;
    for (size_t i = 0; i < v.items.size(); ++i) {
        if (i)
            out += sep;
        out += quote ? "\"" + escape_string_cstyle(v.items[i]) + "\"" : v.items[i];
    }
    return out;
}

nb::object opt_none_str(const std::string &s) { return s.empty() ? nb::object(nb::none()) : nb::object(nb::str(s.c_str())); }

// ---- loading a PresetDict ----------------------------------------------------------------------------------

struct Loaded {
    DynamicPrintConfig                          layer;        // known keys only
    std::vector<std::pair<std::string, std::string>> unknown; // pass-through (key, serialized value)
    std::map<std::string, std::string>          bad;          // key -> message, for values that did not parse
    ConfigSubstitutionContext                   ctx{ForwardCompatibilitySubstitutionRule::EnableSilent};
};

enum class KeyKind { Known, LegacyDropped, Unknown };

// Orca's handle_legacy() maps renamed keys and clears the obsolete ones (they stay in the definition but are
// ignored on load, e.g. silent_mode). Anything the definition does not know at all is Unknown.
KeyKind classify(const std::string &key, const std::string &value)
{
    std::string k = key, v = value;
    PrintConfigDef::handle_legacy(k, v);
    if (k.empty())
        return print_config_def.has(key) ? KeyKind::LegacyDropped : KeyKind::Unknown;
    if (print_config_def.has(k))
        return KeyKind::Known;
    for (const auto &opt : print_config_def.options)
        for (const t_config_option_key &alias : opt.second.aliases)
            if (alias == k)
                return KeyKind::Known;
    return KeyKind::Unknown;
}

// Loads into an EMPTY DynamicPrintConfig, never into full_print_config(): deserialising straight into the default
// instances dereferences a null enum keys_map (02 section 5.1). `only` restricts to a key set (nullptr = all).
void load_into(Loaded &out, const std::vector<std::pair<std::string, Value>> &items, const std::unordered_set<std::string> *only,
               bool skip_metadata)
{
    for (const auto &kv : items) {
        const std::string &key = kv.first;
        if (skip_metadata && metadata_keys().count(key))
            continue;
        const std::string value = serialize_value(key, kv.second);
        if (only && !only->count(key))
            continue;
        switch (classify(key, value)) {
        case KeyKind::LegacyDropped: continue;
        case KeyKind::Unknown: out.unknown.emplace_back(key, value); continue;
        case KeyKind::Known: break;
        }
        try {
            out.layer.set_deserialize(key, value, out.ctx);
        } catch (const UnknownOptionException &) {
            out.unknown.emplace_back(key, value);
        } catch (const BadOptionValueException &e) {
            out.bad[key] = e.what();
        } catch (const ConfigurationError &e) {
            out.bad[key] = e.what();
        }
    }
}

std::unordered_set<std::string> key_set(const std::vector<std::string> &keys) { return {keys.begin(), keys.end()}; }

const std::unordered_set<std::string> &filament_keys()
{
    static const auto s = key_set(Preset::filament_options());
    return s;
}

nb::dict flat_dict(const DynamicPrintConfig &cfg)
{
    nb::dict out;
    for (const std::string &key : cfg.keys()) {
        const std::string value = cfg.opt_serialize(key);
        if (classify(key, value) == KeyKind::LegacyDropped)
            continue;  // obsolete keys are not part of a normalized config
        // Enum values that are not in the option's key map serialize to nothing (the default of
        // overhang_fan_threshold, Overhang_threshold_bridge, is one), giving text such as "," that does not
        // deserialize. Such a value is left out, so the consumer falls back to the same default.
        // (Probe with an option made from the definition: the default instances inside full_print_config() have a
        // null keys_map for enum vectors and crash in deserialize, see load_into.)
        if (const ConfigOptionDef *def = print_config_def.get(key); def && (def->type == coEnum || def->type == coEnums)) {
            if (value.find_first_not_of(", ") == std::string::npos)
                continue;  // nothing representable at all
            std::unique_ptr<ConfigOption> probe;
            try {
                probe.reset(def->create_empty_option());
            } catch (const ConfigurationError &) {
                // nullable enums have no empty-option form in Orca; keep their value as it is
            }
            if (probe && !probe->deserialize(value))
                continue;
        }
        out[key.c_str()] = nb::str(value.c_str());
    }
    return out;
}

std::string preset_name(const std::vector<std::pair<std::string, Value>> &items)
{
    for (const auto &kv : items)
        if (kv.first == "name" && !kv.second.is_list && !kv.second.scalar.empty())
            return kv.second.scalar;
    return "unnamed";
}

[[noreturn]] void raise_bad(const std::string &key, const std::string &message, const std::vector<std::pair<std::string, Value>> *items)
{
    std::string value;
    if (items)
        for (const auto &kv : *items)
            if (kv.first == key)
                value = serialize_value(key, kv.second);
    raise_config_error("invalid value for '" + key + "': " + message, key, value);
}

// ---- compose_config ----------------------------------------------------------------------------------------

Preset build_preset(Preset::Type type, const std::vector<std::string> &base_keys, const std::vector<std::pair<std::string, Value>> &items,
                    const std::unordered_set<std::string> *only, std::vector<std::pair<std::string, std::string>> *unknown)
{
    Preset preset(type, preset_name(items));
    std::unique_ptr<DynamicPrintConfig> cfg(DynamicPrintConfig::new_from_defaults_keys(base_keys));
    Loaded loaded;
    load_into(loaded, items, only, true);
    if (!loaded.bad.empty())
        raise_bad(loaded.bad.begin()->first, loaded.bad.begin()->second, &items);
    cfg->apply(loaded.layer, true);
    if (unknown)
        unknown->insert(unknown->end(), loaded.unknown.begin(), loaded.unknown.end());
    preset.config = std::move(*cfg);
    return preset;
}

nb::dict compose_config(nb::handle printer, nb::handle process, nb::handle filaments, nb::handle project)
{
    CNumericLocalesSetter locales;
    if (!nb::isinstance<nb::list>(filaments) && !nb::isinstance<nb::tuple>(filaments))
        throw nb::type_error("filaments must be a list of dicts");
    std::vector<std::vector<std::pair<std::string, Value>>> filament_items;
    for (nb::handle f : filaments)
        filament_items.push_back(read_dict(f, false, "filament preset"));
    if (filament_items.empty())
        throw nb::value_error("compose_config needs at least one filament");
    const auto printer_items = read_dict(printer, false, "printer preset");
    const auto process_items = read_dict(process, false, "process preset");
    std::vector<std::pair<std::string, Value>> project_items;
    if (!project.is_none())
        project_items = read_dict(project, false, "project");

    std::vector<std::pair<std::string, std::string>> unknown;
    Preset printer_preset = build_preset(Preset::TYPE_PRINTER, Preset::printer_options(), printer_items, nullptr, &unknown);
    Preset print_preset   = build_preset(Preset::TYPE_PRINT, Preset::print_options(), process_items, nullptr, &unknown);
    std::vector<Preset> filament_presets;
    for (const auto &items : filament_items)
        // Non-filament keys are filtered out first (02 section 5.1); this also avoids the null dereference at
        // PresetBundle.cpp:280 for keys the composed config does not have.
        filament_presets.push_back(build_preset(Preset::TYPE_FILAMENT, Preset::filament_options(), items, &filament_keys(), nullptr));

    // `project` is applied last, verbatim; filament_map is also needed up front by the composition.
    Loaded project_loaded;
    load_into(project_loaded, project_items, nullptr, false);
    if (!project_loaded.bad.empty())
        raise_bad(project_loaded.bad.begin()->first, project_loaded.bad.begin()->second, &project_items);
    std::optional<std::vector<int>> filament_maps;
    if (const auto *fm = project_loaded.layer.option<ConfigOptionInts>("filament_map"))
        filament_maps = fm->values;

    DynamicPrintConfig empty_project;
    DynamicPrintConfig out = PresetBundle::construct_full_config(printer_preset, print_preset, empty_project, filament_presets,
                                                                 /*apply_extruder=*/false, filament_maps);
    // Keys that live in Orca's project config but belong to a filament slot (the caller puts the slot colour in
    // the slot's dict, 04 section 6.1): concatenated in slot order, defaulted per slot when absent.
    for (const char *key : {"filament_colour", "filament_colour_type", "filament_multi_colour"}) {
        const ConfigOptionDef *def = print_config_def.get(key);
        const auto *dflt = def ? def->get_default_value<ConfigOptionStrings>() : nullptr;
        std::vector<std::string> values;
        for (const auto &items : filament_items) {
            std::string v = (dflt && !dflt->values.empty()) ? dflt->values.front() : std::string();
            for (const auto &kv : items)
                if (kv.first == key)
                    v = kv.second.is_list ? (kv.second.items.empty() ? v : kv.second.items.front()) : kv.second.scalar;
            values.push_back(v);
        }
        out.option<ConfigOptionStrings>(key, true)->values = std::move(values);
    }
    out.apply(project_loaded.layer, true);
    unknown.insert(unknown.end(), project_loaded.unknown.begin(), project_loaded.unknown.end());

    nb::dict result = flat_dict(out);
    for (const auto &kv : unknown)
        result[kv.first.c_str()] = nb::str(kv.second.c_str());
    return result;
}

// ---- normalize_config --------------------------------------------------------------------------------------

nb::dict normalize_config(nb::handle flat)
{
    CNumericLocalesSetter locales;
    const auto items = read_dict(flat, false, "config");

    Loaded loaded;
    load_into(loaded, items, nullptr, false);

    DynamicPrintConfig cfg = DynamicPrintConfig::full_print_config();
    cfg.apply(loaded.layer, true);
    cfg.handle_legacy_composite();
    cfg.normalize_fdm();

    nb::dict errors_out, out;
    for (const auto &kv : loaded.bad)
        errors_out[kv.first.c_str()] = nb::str(kv.second.c_str());
    for (const auto &kv : cfg.validate())
        errors_out[kv.first.c_str()] = nb::str(kv.second.c_str());

    nb::list substitutions;
    for (const ConfigSubstitution &s : loaded.ctx.substitutions) {
        nb::dict d;
        d["key"] = nb::str(s.opt_def->opt_key.c_str());
        d["value"] = nb::str(s.old_value.c_str());
        d["replacement"] = nb::str(s.new_value ? s.new_value->serialize().c_str() : "");
        substitutions.append(d);
    }

    nb::list issues;
    auto add_unknown = [&](const std::string &key) {
        nb::dict i;
        i["level"] = "warning";
        i["code"] = "unknown_key";
        i["message"] = nb::str(("unknown configuration key '" + key + "' was dropped").c_str());
        i["opt_key"] = nb::str(key.c_str());
        i["object_name"] = nb::none();
        issues.append(i);
    };
    std::set<std::string> reported;
    for (const auto &kv : loaded.unknown)
        if (reported.insert(kv.first).second)
            add_unknown(kv.first);

    out["config"] = flat_dict(cfg);
    out["substitutions"] = substitutions;
    out["errors"] = errors_out;
    out["issues"] = issues;
    return out;
}

// ---- eval_condition ----------------------------------------------------------------------------------------

// A DynamicConfig for PlaceholderParser. Keys the schema knows are deserialised into their real type; others
// (printer_preset, num_extruders, ...) get the narrowest type their text allows, like Orca's own callers do
// (Preset::is_compatible_with_printer builds num_extruders as an int and printer_preset as a string).
class Condition {
public:
    explicit Condition(nb::handle config)
    {
        CNumericLocalesSetter locales;
        const auto items = read_dict(config, /*lenient=*/true, "config");
        Loaded loaded;
        load_into(loaded, items, nullptr, false);
        if (!loaded.bad.empty())
            raise_bad(loaded.bad.begin()->first, loaded.bad.begin()->second, &items);
        m_cfg.apply(loaded.layer, true);
        for (const auto &kv : items) {
            if (print_config_def.has(kv.first))
                continue;
            const Value &v = kv.second;
            if (v.is_list) {
                m_cfg.set_key_value(kv.first, new ConfigOptionStrings(v.items));
            } else if (int i; parse_int(v.scalar, i)) {
                m_cfg.set_key_value(kv.first, new ConfigOptionInt(i));
            } else if (double d; parse_double(v.scalar, d)) {
                m_cfg.set_key_value(kv.first, new ConfigOptionFloat(d));
            } else {
                m_cfg.set_key_value(kv.first, new ConfigOptionString(v.scalar));
            }
        }
    }

    bool eval(const std::string &expr) const
    {
        CNumericLocalesSetter locales;
        try {
            return PlaceholderParser::evaluate_boolean_expression(expr, m_cfg);
        } catch (const std::exception &e) {
            raise_config_error(std::string("cannot evaluate condition: ") + e.what(), "", expr);
        }
    }

private:
    static bool parse_int(const std::string &s, int &out)
    {
        if (s.empty())
            return false;
        char *end = nullptr;
        long v = std::strtol(s.c_str(), &end, 10);
        if (*end != 0)
            return false;
        out = int(v);
        return true;
    }
    static bool parse_double(const std::string &s, double &out)
    {
        if (s.empty())
            return false;
        char *end = nullptr;
        double v = std::strtod(s.c_str(), &end);
        if (*end != 0)
            return false;
        out = v;
        return true;
    }

    DynamicPrintConfig m_cfg;
};

// ---- config_schema -----------------------------------------------------------------------------------------

const char *type_name(ConfigOptionType t)
{
    switch (t) {
    case coFloat: return "float";
    case coFloats: return "floats";
    case coInt: return "int";
    case coInts: return "ints";
    case coString: return "string";
    case coStrings: return "strings";
    case coPercent: return "percent";
    case coPercents: return "percents";
    case coFloatOrPercent: return "floatOrPercent";
    case coFloatsOrPercents: return "floatsOrPercents";
    case coPoint: return "point";
    case coPoints: return "points";
    case coPoint3: return "point3";
    case coBool: return "bool";
    case coBools: return "bools";
    case coEnum: return "enum";
    case coEnums: return "enums";
    case coIntsGroups: return "intsGroups";
    case coPointsGroups: return "pointsGroups";
    default: return "none";
    }
}

const char *mode_name(ConfigOptionMode m)
{
    switch (m) {
    case comSimple: return "simple";
    case comAdvanced: return "advanced";
    case comExpert: return "expert";
    default: return "develop";
    }
}

const char *gui_type_name(ConfigOptionDef::GUIType t)
{
    switch (t) {
    case ConfigOptionDef::GUIType::i_enum_open: return "i_enum_open";
    case ConfigOptionDef::GUIType::f_enum_open: return "f_enum_open";
    case ConfigOptionDef::GUIType::color: return "color";
    case ConfigOptionDef::GUIType::select_open: return "select_open";
    case ConfigOptionDef::GUIType::slider: return "slider";
    case ConfigOptionDef::GUIType::legend: return "legend";
    case ConfigOptionDef::GUIType::one_string: return "one_string";
    default: return "";
    }
}

struct Membership {
    std::unordered_set<std::string> print, filament, printer, object, region;
    Membership()
    {
        for (const auto &k : Preset::print_options()) print.insert(k);
        for (const auto &k : Preset::filament_options()) filament.insert(k);
        for (const auto &k : Preset::printer_options()) printer.insert(k);
        for (const auto &k : PrintObjectConfig::defaults().keys()) object.insert(k);
        for (const auto &k : PrintRegionConfig::defaults().keys()) region.insert(k);
    }
};

nb::dict config_schema()
{
    CNumericLocalesSetter locales;
    static const Membership mem;
    nb::dict out;
    for (const auto &kv : print_config_def.options) {
        const std::string &key = kv.first;
        const ConfigOptionDef &d = kv.second;
        nb::dict e;
        e["type"] = type_name(d.type);
        e["label"] = nb::str(d.label.c_str());
        e["full_label"] = nb::str(d.full_label.c_str());
        e["category"] = nb::str(d.category.c_str());
        e["tooltip"] = nb::str(d.tooltip.c_str());
        e["sidetext"] = nb::str(d.sidetext.c_str());
        e["min"] = d.min <= -FLT_MAX ? nb::object(nb::none()) : nb::object(nb::float_(d.min));
        e["max"] = d.max >= FLT_MAX ? nb::object(nb::none()) : nb::object(nb::float_(d.max));
        e["max_literal"] = nb::float_(d.max_literal);
        std::string def_str;
        if (d.default_value) {
            try {
                def_str = d.default_value->serialize();
            } catch (const std::exception &) {
            }
        }
        e["default"] = nb::str(def_str.c_str());
        if (d.enum_values.empty()) {
            e["enum"] = nb::none();
        } else {
            nb::list values;
            for (size_t i = 0; i < d.enum_values.size(); ++i) {
                nb::dict item;
                item["value"] = nb::str(d.enum_values[i].c_str());
                item["label"] = nb::str((i < d.enum_labels.size() ? d.enum_labels[i] : d.enum_values[i]).c_str());
                values.append(item);
            }
            e["enum"] = values;
        }
        e["enum_open"] = d.gui_type == ConfigOptionDef::GUIType::i_enum_open || d.gui_type == ConfigOptionDef::GUIType::f_enum_open ||
                         d.gui_type == ConfigOptionDef::GUIType::select_open;
        e["gui_type"] = gui_type_name(d.gui_type);
        e["gui_flags"] = nb::str(d.gui_flags.c_str());
        e["multiline"] = d.multiline;
        e["full_width"] = d.full_width;
        e["is_code"] = d.is_code;
        e["readonly"] = d.readonly;
        e["nullable"] = d.nullable;
        e["ratio_over"] = opt_none_str(d.ratio_over);
        e["mode"] = mode_name(d.mode);
        // "Per extruder": a vector option that holds one value per extruder or filament (not geometry lists).
        e["per_extruder"] = !d.is_scalar() && d.type != coPoints && d.type != coPointsGroups && d.type != coIntsGroups;
        e["preset"] = mem.print.count(key) ? "process" : mem.filament.count(key) ? "filament" : mem.printer.count(key) ? "printer" : "none";
        e["scope"] = mem.object.count(key) ? "object" : mem.region.count(key) ? "region" : "global";
        e["variant"] = print_options_with_variant.count(key)      ? "print"
                       : filament_options_with_variant.count(key) ? "filament"
                       : printer_options_with_variant_1.count(key) ? "printer1"
                       : printer_options_with_variant_2.count(key) ? "printer2"
                                                                   : "none";
        out[key.c_str()] = e;
    }
    return out;
}

} // namespace

void bind_config(nb::module_ &m)
{
    m.def("config_schema", &config_schema);
    m.def(
        "normalize_config", [](nb::handle flat) { return normalize_config(flat); }, nb::arg("flat"));
    m.def(
        "compose_config",
        [](nb::handle printer, nb::handle process, nb::handle filaments, nb::handle project) {
            return compose_config(printer, process, filaments, project);
        },
        nb::arg("printer"), nb::arg("process"), nb::arg("filaments"), nb::arg("project") = nb::none());
    m.def(
        "eval_condition", [](const std::string &expr, nb::handle config) { return Condition(config).eval(expr); }, nb::arg("expr"),
        nb::arg("config"));

    nb::class_<Condition>(m, "ConditionContext")
        .def(nb::init<nb::handle>(), nb::arg("config"))
        .def("eval", &Condition::eval, nb::arg("expr"));
}

} // namespace slicewright
