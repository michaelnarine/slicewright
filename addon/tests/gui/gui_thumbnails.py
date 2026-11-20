# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI Blender: the plate thumbnail render (03 section 6.2) produces a non-blank image of the right size.

Draws a plate of two coloured objects into a ``GPUOffScreen`` for the sizes a printer typically asks for and checks
the engine's image layout: uint8 (H, W, 4), transparent corners, an opaque and coloured middle, the plate inside the
frame, each object's colour present, and the top row of the array being the top of the picture. PNGs go to the output
directory for a look.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_common as g  # noqa: E402

SIZES = [(48, 48), (300, 300), (160, 120), (300, 48)]


def box(x, y, size, height):
    import numpy as np
    r = size / 2
    v = np.array([(x - r, y - r, 0), (x + r, y - r, 0), (x + r, y + r, 0), (x - r, y + r, 0),
                  (x - r, y - r, height), (x + r, y - r, height), (x + r, y + r, height),
                  (x - r, y + r, height)], np.float32)
    t = np.array([(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
                  (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)], np.int32)
    return v, t


def main():
    import numpy as np
    from slicewright.blender import thumbnails
    from slicewright.core import thumbnails as core

    meshes = [(*box(100, 100, 30, 60), (1.0, 0.1, 0.1)), (*box(160, 120, 20, 10), (0.1, 0.2, 1.0))]
    images = thumbnails.render_plate(meshes, SIZES)
    g.check("one_image_per_size", len(images) == len(SIZES))
    for (w, h), img in zip(SIZES, images):
        tag = f"{w}x{h}"
        g.check(f"{tag}_shape_dtype", img.shape == (h, w, 4) and img.dtype == np.uint8 and img.flags["C_CONTIGUOUS"],
                shape=list(img.shape), dtype=str(img.dtype))
        alpha = img[..., 3]
        covered = float((alpha > 0).mean())
        g.check(f"{tag}_not_blank", 0.02 < covered < 0.98, covered=round(covered, 3))
        g.check(f"{tag}_corners_transparent", all(alpha[y, x] == 0 for y in (0, h - 1) for x in (0, w - 1)))
        g.check(f"{tag}_solid_where_drawn", int((alpha == 255).sum()) > 0.8 * int((alpha > 0).sum()),
                solid=int((alpha == 255).sum()), touched=int((alpha > 0).sum()))      # no blending: opaque or empty
        rgb = img[..., :3].astype(int)[alpha == 255]
        red = int(((rgb[:, 0] > rgb[:, 2] + 40)).sum())
        blue = int(((rgb[:, 2] > rgb[:, 0] + 40)).sum())
        g.check(f"{tag}_both_colours", red > 0 and (blue > 0 or w < 100), red=red, blue=blue)
        g.write_png(os.path.join(g.OUT, f"thumb_{tag}.png"), img, flip=False)     # already top-down
    # orientation: the plate is a tall box, so its picture is taller than wide and the array's first rows are above it
    big = images[1]
    ys, xs = np.nonzero(big[..., 3] > 0)
    g.check("fits_the_frame_with_a_margin", ys.min() > 0 and xs.min() > 0 and ys.max() < 299 and xs.max() < 299,
            box=[int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())])
    top_row_colour = big[ys.min() + 2][big[ys.min() + 2][:, 3] > 0][:, :3].mean(axis=0)
    g.check("top_of_the_array_is_the_top_of_the_picture", top_row_colour[0] > top_row_colour[2],
            colour=[round(float(c)) for c in top_row_colour])        # the tall red box reaches highest
    g.check("sizes_match_the_config_parser", core.distinct_sizes(core.parse_specs("300x300/PNG,160x120/PNG")) == [(300, 300), (160, 120)])


g.run(main)
