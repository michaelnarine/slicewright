// SPDX-License-Identifier: AGPL-3.0-only
#include "thumbnails.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>

#include "arrays.hpp"

#include "libslic3r/GCode/Thumbnails.hpp"

namespace slicewright {

using namespace Slic3r;

ThumbnailSet parse_thumbnails(nb::handle images)
{
    if (!nb::isinstance<nb::list>(images) && !nb::isinstance<nb::tuple>(images))
        throw nb::type_error("images must be a list of uint8 (H, W, 4) arrays");
    ThumbnailSet out;
    for (nb::handle h : images) {
        const AnyArray a = as_array(h, "thumbnail");
        if (!is_dtype<uint8_t>(a))
            throw nb::type_error("a thumbnail must have dtype uint8");
        if (a.ndim() != 3 || a.shape(2) != 4)
            throw nb::value_error("a thumbnail must have shape (H, W, 4)");
        const size_t height = a.shape(0), width = a.shape(1);
        if (height == 0 || width == 0)
            throw nb::value_error("a thumbnail must not be empty");
        if ((height > 1 && a.stride(0) != int64_t(width * 4)) || (width > 1 && a.stride(1) != 4) || a.stride(2) != 1)
            throw nb::type_error("a thumbnail must be C-contiguous");
        ThumbnailData d;
        d.set(unsigned(width), unsigned(height));
        const uint8_t *src = static_cast<const uint8_t *>(a.data());
        const size_t row = width * 4;
        for (size_t y = 0; y < height; ++y)  // the API's row 0 is the top, Orca's is the bottom
            std::memcpy(d.pixels.data() + (height - 1 - y) * row, src + y * row, row);
        out.images.push_back(std::move(d));
    }
    out.provided = !out.images.empty();
    return out;
}

std::vector<Issue> check_thumbnails(const ThumbnailSet &set, const DynamicPrintConfig &cfg, bool bbl_printer)
{
    std::vector<Issue> out;
    if (!set.provided || bbl_printer)
        return out;
    auto [wanted, errors] = GCodeThumbnails::make_and_check_thumbnail_list(cfg);
    if (errors != enum_bitmask<ThumbnailError>()) {
        out.push_back({"error", "validation", "Invalid thumbnails value:" + GCodeThumbnails::get_error_string(errors), "thumbnails", ""});
        return out;
    }
    for (const auto &entry : wanted) {
        const unsigned w = unsigned(std::lround(entry.second.x())), h = unsigned(std::lround(entry.second.y()));
        const bool have = std::any_of(set.images.begin(), set.images.end(), [&](const ThumbnailData &d) { return d.width == w && d.height == h; });
        if (!have)
            out.push_back({"warning", "thumbnail_missing",
                           "No image of " + std::to_string(w) + "x" + std::to_string(h) + " was provided for the 'thumbnails' entry; it is skipped.",
                           "thumbnails", ""});
    }
    return out;
}

ThumbnailsGeneratorCallback thumbnail_callback(const ThumbnailSet &set)
{
    if (!set.provided)
        return nullptr;  // exactly the behaviour without thumbnails: no callback, nothing written
    return [&set](const ThumbnailsParams &params) {
        ThumbnailsList list;
        for (const Vec2d &size : params.sizes) {
            const unsigned w = unsigned(std::lround(size.x())), h = unsigned(std::lround(size.y()));
            for (const ThumbnailData &d : set.images)
                if (d.width == w && d.height == h) {
                    list.push_back(d);
                    break;
                }
        }
        return list;
    };
}

const ThumbnailData *largest_thumbnail(const ThumbnailSet &set)
{
    const ThumbnailData *best = nullptr;
    for (const ThumbnailData &d : set.images)
        if (!best || size_t(d.width) * d.height > size_t(best->width) * best->height)
            best = &d;
    return best;
}

} // namespace slicewright
