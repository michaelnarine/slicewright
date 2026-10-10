# SPDX-License-Identifier: GPL-3.0-or-later
"""The add-on's show/enable rule table only names keys the engine's schema has (03 section 2.4).

Runs against every available backend, so the real engine's ~800-key schema checks the table once the
wheel is installed (plan M2); the fake carries the keys the rules need.
"""
from slicewright.blender import settings_rules as rules


def test_every_key_named_by_a_rule_exists_in_the_schema(backend):
    schema = backend.config_schema()
    missing = sorted(rules.rule_keys() - set(schema))
    assert not missing, f"rules name keys the {backend._contract_backend_name} schema lacks: {missing}"


def test_every_key_a_condition_reads_exists_in_the_schema(backend):
    schema = backend.config_schema()
    missing = sorted(rules.condition_keys() - set(schema))
    assert not missing, f"conditions read keys the {backend._contract_backend_name} schema lacks: {missing}"


def test_enum_values_the_rules_compare_with_are_valid_values(backend):
    """A condition like ``wall_generator == "arachne"`` silently never matches if the enum spelling is wrong."""
    schema = backend.config_schema()
    for key, values in rules.ENUM_VALUES_USED.items():
        allowed = {e["value"] for e in schema[key]["enum"]}
        assert set(values) <= allowed, (key, sorted(set(values) - allowed))
