# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare reference screenshots across backends: SSIM (>= 0.98) and share of pixels with
CIEDE2000 > 5 (<= 0.5 %). Run outside Blender, e.g.

    uv run --python 3.13 --with scikit-image --with pillow --with numpy \
        spikes/gpu/compare.py --out report.json metal=results/metal gl=results/opengl vk=results/vulkan
"""
import argparse
import itertools
import json
import sys

import numpy as np
from PIL import Image
from skimage.color import deltaE_ciede2000, rgb2lab
from skimage.metrics import structural_similarity

IMAGES = ["ref_roles.png", "ref_range.png", "ref_boundary_zoom.png"]
SSIM_MIN, DE_SHARE_MAX, DE_THRESHOLD = 0.98, 0.005, 5.0


def load(p):
    return np.asarray(Image.open(p).convert("RGB"), dtype=np.float64) / 255.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="compare.json")
    ap.add_argument("sets", nargs="+", help="name=dir")
    a = ap.parse_args()
    dirs = dict(s.split("=", 1) for s in a.sets)
    rows, ok = [], True
    for (na, da), (nb, db) in itertools.combinations(dirs.items(), 2):
        for img in IMAGES:
            A, B = load(f"{da}/{img}"), load(f"{db}/{img}")
            if A.shape != B.shape:
                rows.append(dict(pair=f"{na}-{nb}", image=img, error=f"shape {A.shape} vs {B.shape}", **{"pass": False}))
                ok = False
                continue
            ssim = float(structural_similarity(A, B, channel_axis=2, data_range=1.0))
            de = deltaE_ciede2000(rgb2lab(A), rgb2lab(B))
            share = float((de > DE_THRESHOLD).mean())
            p = ssim >= SSIM_MIN and share <= DE_SHARE_MAX
            ok &= p
            rows.append({"pair": f"{na}-{nb}", "image": img, "ssim": round(ssim, 5),
                         "de2000_gt5_share_pct": round(share * 100, 4), "de_max": round(float(de.max()), 2),
                         "pass": bool(p)})
            print(rows[-1])
    json.dump(dict(thresholds=dict(ssim_min=SSIM_MIN, de_share_max_pct=DE_SHARE_MAX * 100, de_threshold=DE_THRESHOLD),
                   rows=rows, all_pass=bool(ok)), open(a.out, "w"), indent=1)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
