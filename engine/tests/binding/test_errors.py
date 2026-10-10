# SPDX-License-Identifier: AGPL-3.0-only
"""04 section 7: a native exception thrown on the calling thread reaches Python as the mapped class, with its
attributes. (A translator that throws instead of setting the error is reported by nanobind as a generic RuntimeError.)"""
import pytest

sc = pytest.importorskip("slicewright_engine")
NATIVE = sc._native


@pytest.mark.parametrize("kind", ["configuration", "unknown_option", "bad_option_type", "placeholder"])
def test_orcas_configuration_errors_surface_as_config_error(kind):
    with pytest.raises(sc.ConfigError) as err:
        NATIVE._throw_native(kind, "the message")
    assert "the message" in err.value.message
    assert err.value.key is None and err.value.value is None
    assert isinstance(err.value, sc.Error)


def test_a_canceled_print_surfaces_as_cancelled():
    with pytest.raises(sc.Cancelled):
        NATIVE._throw_native("canceled")


@pytest.mark.parametrize("kind", ["slicing", "slicing_errors"])
def test_a_slicing_error_surfaces_as_slice_error(kind):
    with pytest.raises(sc.SliceError) as err:
        NATIVE._throw_native(kind, "no layers")
    assert err.value.message == "no layers" and err.value.object_name is None


def test_any_other_native_exception_surfaces_as_engine_error_with_its_type():
    with pytest.raises(sc.EngineError) as err:
        NATIVE._throw_native("runtime", "boom")
    assert err.value.message == "boom" and "runtime_error" in err.value.detail


def test_the_mapped_errors_are_not_the_generic_runtime_error():
    # a RuntimeError that is not one of ours would mean the translator threw instead of setting the error
    with pytest.raises(sc.Error):
        NATIVE._throw_native("placeholder")
