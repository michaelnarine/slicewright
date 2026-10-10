// SPDX-License-Identifier: AGPL-3.0-only
// Validation of the numpy arguments of add_object (04 section 2.4): a wrong dtype, shape or contiguity must come
// back as TypeError / ValueError synchronously, which nanobind's typed ndarray parameters cannot do (they turn
// every mismatch into one TypeError about incompatible function arguments).
#pragma once

#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>

#include <cstdint>
#include <string>

namespace slicewright {

namespace nb = nanobind;

using AnyArray = nb::ndarray<nb::device::cpu>;

// The array behind `h`; TypeError if `h` is not an array (a list, say). No implicit conversion.
inline AnyArray as_array(nb::handle h, const char *what)
{
    if (!nb::ndarray_check(h))
        throw nb::type_error((std::string(what) + " must be a numpy array").c_str());
    return nb::cast<AnyArray>(h, /*convert=*/false);
}

template <typename T> inline bool is_dtype(const AnyArray &a) { return a.dtype() == nb::dtype<T>(); }

// A C-contiguous (n, cols) array of T, or (n,) when cols == 0. TypeError for the dtype or contiguity, ValueError
// for the shape.
template <typename T> inline const T *checked(const AnyArray &a, const char *what, const char *dtype_name, size_t cols)
{
    if (!is_dtype<T>(a))
        throw nb::type_error((std::string(what) + " must have dtype " + dtype_name).c_str());
    if (cols == 0) {
        if (a.ndim() != 1)
            throw nb::value_error((std::string(what) + " must have shape (N,)").c_str());
        if (a.shape(0) > 1 && a.stride(0) != 1)
            throw nb::type_error((std::string(what) + " must be C-contiguous").c_str());
    } else {
        if (a.ndim() != 2 || a.shape(1) != cols)
            throw nb::value_error((std::string(what) + " must have shape (N, " + std::to_string(cols) + ")").c_str());
        if ((a.shape(0) > 1 && a.stride(0) != int64_t(cols)) || (cols > 1 && a.stride(1) != 1))
            throw nb::type_error((std::string(what) + " must be C-contiguous").c_str());
    }
    return static_cast<const T *>(a.data());
}

} // namespace slicewright
