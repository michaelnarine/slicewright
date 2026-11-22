# SPDX-License-Identifier: GPL-3.0-or-later
"""The per-object override surface of ``add_object`` against both engines (04 sections 2.5 and 4.1): every key the
add-on offers as an override is accepted, and keys it never offers are refused."""
import pytest
from contract_helpers import box, flat_config
from slicewright.core import object_overrides as oo


def _job(sc):
    job = sc.SliceJob()
    job.set_config(flat_config(sc))
    return job


def test_every_offerable_key_is_accepted_with_its_default_value(jobs_backend):
    sc = jobs_backend
    schema = sc.config_schema()
    keys = oo.offerable(schema, "expert", show_develop=True)
    assert len(keys) > 5
    v, t = box()
    refused = []
    for key in keys:
        try:
            _job(sc).add_object("cube", v, t, config_overrides={key: schema[key]["default"]})
        except Exception as exc:  # noqa: BLE001 - the list below names them
            refused.append((key, f"{type(exc).__name__}: {exc}"))
    assert not refused, refused


def test_keys_that_are_not_per_object_are_refused(jobs_backend):
    sc = jobs_backend
    schema = sc.config_schema()
    global_keys = [k for k, e in schema.items() if not oo.is_per_object(schema, k)][:25]
    v, t = box()
    for key in global_keys:
        with pytest.raises(sc.ConfigError):
            _job(sc).add_object("cube", v, t, config_overrides={key: schema[key]["default"]})
