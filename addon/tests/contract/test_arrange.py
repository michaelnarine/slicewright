# SPDX-License-Identifier: GPL-3.0-or-later
"""SliceJob.arrange and Placement (04 section 4.5)."""
from __future__ import annotations

import numpy as np
import pytest
from contract_helpers import box, drive, new_job, sliced


@pytest.fixture
def sc(jobs_backend):
    return jobs_backend


def _bboxes(placements, objects):
    """XY bounding boxes of ``objects`` after applying each placement transform."""
    out = []
    for p in placements:
        _, v, _ = objects[p["index"]]
        homog = np.c_[v.astype(np.float64), np.ones(len(v))]
        moved = (p["transform"] @ homog.T).T[:, :3]
        out.append((moved[:, 0].min(), moved[:, 1].min(), moved[:, 0].max(), moved[:, 1].max()))
    return out


def _overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _stacked_plate(n=4, size=40.0):
    return [(f"obj{i}", *box(cx=128.0, cy=128.0, size=size)) for i in range(n)]


def test_placement_shape(sc):
    objs = _stacked_plate(2)
    placements = new_job(sc, objs).arrange()
    assert len(placements) == 2
    for i, p in enumerate(placements):
        assert set(p) >= {"index", "name", "transform", "translation", "rotation_z"}
        assert p["index"] == i and p["name"] == objs[i][0]
        t = p["transform"]
        assert isinstance(t, np.ndarray) and t.shape == (4, 4) and t.dtype == np.float64
        assert np.allclose(t[3], [0, 0, 0, 1])
        assert np.allclose(p["translation"], t[:3, 3])
        assert isinstance(p["rotation_z"], float)


def test_arranged_objects_do_not_overlap_and_stay_on_the_bed(sc):
    objs = _stacked_plate(4)
    placements = new_job(sc, objs).arrange(spacing_mm=5.0)
    boxes = _bboxes(placements, objs)
    for i in range(len(boxes)):
        x0, y0, x1, y1 = boxes[i]
        assert x0 >= -1e-3 and y0 >= -1e-3 and x1 <= 256 + 1e-3 and y1 <= 256 + 1e-3
        for j in range(i + 1, len(boxes)):
            assert not _overlap(boxes[i], boxes[j]), (i, j)


def test_arrange_with_rotation_allowed_still_places_everything(sc):
    objs = _stacked_plate(3)
    assert len(new_job(sc, objs).arrange(allow_rotation=True)) == 3


def test_arrange_does_not_change_the_jobs_objects(sc):
    objs = [("a", *box(cx=100.0)), ("b", *box(cx=100.0))]
    job = new_job(sc, objs)
    job.arrange()
    job.start()
    drive(job)
    r = job.result()
    # the job still slices the original, overlapping layout
    xs = r.moves["position"][:, 0]
    assert 85.0 <= float(xs[r.moves["object_id"] >= 0].min()) <= 100.0


def test_arrange_with_no_objects_returns_nothing(sc):
    assert new_job(sc, objects=[]).arrange() == []


def test_arrange_that_cannot_fit_raises_arrange_error(sc):
    objs = [(f"big{i}", *box(size=200.0)) for i in range(3)]   # 3 x 200 mm on a 256 mm bed
    with pytest.raises(sc.ArrangeError) as err:
        new_job(sc, objs).arrange()
    assert err.value.object_names and set(err.value.object_names) <= {o[0] for o in objs}


def test_arrange_is_only_valid_when_idle(sc):
    job, _ = sliced(sc)
    with pytest.raises(sc.StateError):
        job.arrange()

