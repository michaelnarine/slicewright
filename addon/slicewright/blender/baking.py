# SPDX-License-Identifier: GPL-3.0-or-later
"""Baking the slice into plain Blender data (03 section 7.11): availability, the plan, and the job on the tick runner.

What is baked is the result the Preview shows (``preview.runtime``): its ``moves`` and ``layers``. The plan chooses the
segments (``core/bake.select``: a layer range, the visible feature types, travel only on request), splits them by source
object and estimates the cost (``core/bake_plan``). The job is one generator task on ``timers.runner``: numpy steps of a
few thousand segments, then Blender-side copies (``bake_data``), each ``yield`` ending a step of roughly 20 ms or less.
Nothing is linked to the scene until the last step; that step also removes the objects this add-on tagged in an earlier
bake of the same sources (the operator asked first), so a cancel or a failure leaves the scene exactly as it was.

Baked objects are plain data in the ``Slicewright Bake`` collection: no property or modifier points back at the add-on.
Timers never fire in ``-b``: tests drive ``timers.runner`` themselves.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import bpy

from ..core import bake, bake_plan as bp
from ..core import preview_data as pd
from ..core import preview_nav as nav
from ..names import PACKAGE_ID
from . import bake_data, bake_pg, frame, registry, slicing, timers, units
from .preview import runtime

REQUIRES_ENGINE = True
log = logging.getLogger("slicewright.bake")


class BakeError(Exception):
    """A bake that cannot start; the message is for the user."""


@dataclass
class Plan:
    scene: str
    kind: str
    flt: bake.Filter
    sides: int
    part_segments: int
    groups: list                       # [(object_id, Selection)]
    estimate: bp.Estimate
    sources: list                      # the source name of each group
    existing: list                     # names of the bake objects a re-bake would replace
    result: Any = field(repr=False, default=None)
    types: bake.MoveTypes | None = None
    role_names: dict = field(default_factory=dict)
    scale: float = 1.0                 # Blender units per millimetre
    offset: Any = None                 # bed-centre offset (core.frame): baked points are written in the world frame


@dataclass
class _Job:
    plan: Plan
    task: Any = None
    progress: float = 0.0
    text: str = ""


class _Runtime:
    def __init__(self) -> None:
        self.job: _Job | None = None
        self.messages: dict[str, tuple[str, str]] = {}      # scene name -> (level, text) of the last bake
        self.estimates: dict[str, tuple] = {}               # scene name -> (key, text, level) of the panel's estimate


_rt = _Runtime()


def task_name(scene) -> str:
    return f"bake:{scene.name}"


# -- messages ----------------------------------------------------------------------------------------------------

def message(scene) -> tuple[str, str] | None:
    return _rt.messages.get(scene.name)


def set_message(scene, level: str | None, text: str = "") -> None:
    if level:
        _rt.messages[scene.name] = (level, text)
    else:
        _rt.messages.pop(scene.name, None)
    runtime.tag_redraw()


def progress(scene) -> tuple[float, str] | None:
    job = _rt.job
    return (job.progress, job.text) if job is not None and job.plan.scene == scene.name else None


def is_live() -> bool:
    return _rt.job is not None


# -- availability ------------------------------------------------------------------------------------------------

def availability(scene) -> tuple[bool, str]:
    """``(ok, why not)``: whether a bake can start now, and the reason the panel shows when it cannot."""
    sw = getattr(scene, PACKAGE_ID, None)
    if sw is None:
        return False, ""
    if _rt.job is not None:
        return False, "Wait for the bake to finish"
    if slicing.is_live():
        return False, "Wait for the slice to finish"
    if any(t.name.startswith("cache-load:") for t in timers.runner.pending()):
        return False, "Loading the cached slice…"
    rt = runtime.get(scene)
    if rt is None or rt.result is None or not sw.last_cache_key:
        return False, "Slice the plate to bake it"
    if sw.stale:
        return False, "Out of date: slice again, then bake"
    if rt.n_moves < 2:
        return False, "The slice has no toolpath"
    return True, ""


# -- the plan ----------------------------------------------------------------------------------------------------

def make_filter(st, rt, preview_props) -> bake.Filter:
    """The segments to bake: the Preview's visible range and features, or the panel's own layer range and features."""
    layers = rt.layers
    n = rt.n_moves
    if st.use_preview:
        s = nav.clamp(preview_props.scrub(), layers)
        rng = pd.visible_range(layers, s.lo, s.hi, s.pos, n - 1)
        mask = nav.role_mask(preview_props.role_mask)
    else:
        s = nav.clamp(nav.Scrub(st.layer_lo, st.layer_hi, None), layers)
        rng = pd.visible_range(layers, s.lo, s.hi, None, n - 1)
        mask = nav.role_mask(st.roles)
    first, last = rng if rng is not None else (1, 0)
    return bake.Filter(first, last, mask, bool(st.travel))


def _scale(scene) -> float:
    return 1.0 / units.scene_mm_per_bu(scene)


def make_plan(scene, st, kind: str | None = None) -> Plan:
    """The plan for the current settings. Raises :class:`BakeError` when there is nothing to bake or it is too large."""
    from .preview import props as preview_props
    rt = runtime.get(scene)
    if rt is None or rt.result is None:
        raise BakeError("Slice the plate to bake it")
    kind = kind or st.kind
    enums = registry.state.status.module.enums()
    types = bake.MoveTypes.from_enums(enums)
    flt = make_filter(st, rt, preview_props.of(scene))
    sel = bake.select(rt.moves, flt, types)
    if sel.n_segments == 0:
        raise BakeError("Nothing to bake: the layer range and feature types select no extrusions")
    est = bp.estimate(kind, sel.n_segments, sel.n_paths, st.sides)
    groups = bake.split_groups(rt.moves, sel)
    objects = list(getattr(rt.result, "objects", []) or [])
    sources = [bp.source_name(objects, gid) for gid, _ in groups]
    existing = [ob.name for ob in bake_data.tagged_objects(scene.name, kind, set(sources))]
    return Plan(scene.name, kind, flt, int(st.sides), int(st.part_segments), groups, est, sources, existing, rt.result,
                types, bp.role_names_of(enums), _scale(scene), frame.offset_mm(scene))


def panel_estimate(scene, st) -> tuple[str, str]:
    """``(text, level)`` for the panel: the cost of the current settings, memoised (selecting is a numpy pass over the
    moves, ~0.1 s for 10M of them) and recomputed only when a setting or the result changes."""
    from .preview import props as preview_props
    rt = runtime.get(scene)
    if rt is None or rt.result is None:
        return "", "ok"
    p = preview_props.of(scene)
    key = (id(rt.result), st.kind, st.use_preview, st.layer_lo, st.layer_hi, tuple(st.roles), st.travel, st.sides,
           p.scrub(), tuple(p.role_mask) if st.use_preview else None)
    cached = _rt.estimates.get(scene.name)
    if cached is not None and cached[0] == key:
        return cached[1], cached[2]
    try:
        plan = make_plan(scene, st)
        text, level = bp.verdict_text(plan.estimate), plan.estimate.level
    except BakeError as exc:
        text, level = str(exc), "refuse"
    _rt.estimates[scene.name] = (key, text, level)
    return text, level


def confirm_text(plan: Plan) -> tuple[str, str] | None:
    """``(message, confirm button)`` of the popup this plan needs, or None when it can start without asking."""
    parts = []
    if plan.estimate.level == "confirm":
        parts.append(bp.verdict_text(plan.estimate) + ".")
    if plan.existing:
        n = len(plan.existing)
        parts.append(f"The {n} object{'s' if n != 1 else ''} of the previous bake of the same "
                     f"source{'s' if len(plan.sources) != 1 else ''} will be replaced: "
                     + ", ".join(plan.existing[:4]) + (", …" if n > 4 else "") + ".")
    if not parts:
        return None
    return " ".join(parts), "Replace and bake" if plan.existing else "Bake"


# -- the job -----------------------------------------------------------------------------------------------------

def start(scene, plan: Plan) -> None:
    """Start the job for ``plan`` on the tick runner. The caller has checked :func:`availability` and asked what had to
    be asked; a plan the estimate refuses is not started."""
    if plan.estimate.level == "refuse":
        raise BakeError(bp.verdict_text(plan.estimate))
    job = _Job(plan, text="Baking…")
    _rt.job = job
    set_message(scene, None)
    job.task = timers.runner.submit(task_name(scene), lambda: _work(job), on_done=lambda r: _done(job, r),
                                    on_error=lambda e: _failed(job, e), on_cancel=lambda: _cancelled(job), replace=True)
    log.info("bake started: %s", plan.estimate.describe())


def cancel() -> bool:
    job = _rt.job
    return bool(job is not None and job.task is not None and timers.runner.cancel(job.task))


def _end(job: _Job) -> bpy.types.Scene | None:
    if _rt.job is job:
        _rt.job = None
    runtime.tag_redraw()
    return bpy.data.scenes.get(job.plan.scene)


def _done(job: _Job, result) -> None:
    scene = _end(job)
    if scene is not None:
        n, est = result
        set_message(scene, "INFO", f"Baked {n} object{'s' if n != 1 else ''}: {est.describe()}")
    log.info("bake done")


def _failed(job: _Job, exc: BaseException) -> None:
    scene = _end(job)
    if isinstance(exc, ReferenceError):
        text = "Bake stopped: the file changed while baking (undo?)"
    else:
        text = f"Bake failed: {exc}"
        log.error("bake failed: %s", exc, exc_info=exc)
    if scene is not None:
        set_message(scene, "ERROR", text)


def _cancelled(job: _Job) -> None:
    scene = _end(job)
    if scene is not None:
        set_message(scene, "INFO", "Bake cancelled")


def _step(job: _Job, done: int, total: int, text: str):
    job.progress, job.text = min(done / max(total, 1), 0.99), text
    return (job.progress, text)


def _work(job: _Job):
    plan = job.plan
    moves = plan.result.moves
    created: list = []                       # datablocks to discard unless the bake completes
    outputs: list = []                       # (object name, source, data, feature keys)
    finished = False
    total, done = plan.estimate.segments, 0
    try:
        for (gid, sel), source in zip(plan.groups, plan.sources):
            keys = bake.roles_present(moves, sel, plan.types)
            lut = bake.material_lut(keys)
            if plan.kind == bp.CURVES:
                out = bake.alloc_curves(sel)
                for p0, p1 in bake.path_chunks(sel, bp.STEP_POINTS):
                    bake.fill_curves(out, moves, sel, p0, p1, plan.types, plan.scale, lut, plan.offset)
                    done += int(sel.path_first[p1] if p1 < sel.n_paths else sel.n_segments) - int(sel.path_first[p0])
                    yield _step(job, done, total, f"Baking {source}")
                name = bp.object_name(source, plan.kind)
                curves = yield from _stepping(job, bake_data.build_curves(name, out), done, total, f"Building {source}")
                created.append(curves)
                outputs.append((name, source, curves, keys))
            else:
                ranges = bake.part_ranges(sel.n_segments, plan.part_segments)
                for k, (a, b) in enumerate(ranges):
                    part = bake.alloc_prisms(sel, a, b, plan.sides)
                    for s in range(a, b, bp.STEP_SEGMENTS):
                        bake.fill_prisms(part, moves, sel, s, min(s + bp.STEP_SEGMENTS, b), a, plan.types, plan.scale, lut, plan.offset)
                        yield _step(job, done + s - a, total, f"Baking {source}")
                    bake.fill_caps(part)
                    name = bp.object_name(source, plan.kind, k, len(ranges))
                    mesh = yield from _stepping(job, bake_data.build_mesh(name, part), done + b - a, total,
                                                f"Building {source} ({k + 1} of {len(ranges)})")
                    created.append(mesh)
                    outputs.append((name, source, mesh, keys))
                    done += b - a
        yield _step(job, total, total, "Adding to the scene")
        result = _finish(job, outputs, created)
        finished = True
        return result
    finally:
        if not finished:
            bake_data.discard(created)


def _stepping(job: _Job, gen, done: int, total: int, text: str):
    """Run a ``bake_data`` generator, turning its bare yields into progress steps; returns its value."""
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value
        yield _step(job, done, total, text)


def _finish(job: _Job, outputs: list, created: list):
    """The one step that touches the scene: remove what this bake replaces, then link every new object."""
    plan = job.plan
    scene = bpy.data.scenes.get(plan.scene)
    if scene is None:
        raise BakeError("The scene is gone")
    materials: list = []
    coll = bake_data.bake_collection(scene)
    bake_data.remove_objects(bake_data.tagged_objects(scene.name, plan.kind, set(plan.sources)))
    for name, source, data, keys in outputs:
        for key in keys:
            data.materials.append(bake_data.material_for(key, plan.role_names, materials))
        bake_data.tag(data, plan.kind, source if source != bp.EXTRAS_NAME else "", scene.name)
        ob = bpy.data.objects.new(name, data)
        bake_data.tag(ob, plan.kind, source, scene.name)
        ob[bp.TAG_FEATURES] = bp.feature_names(keys, plan.role_names)
        coll.objects.link(ob)
    try:
        bpy.ops.ed.undo_push(message="Bake toolpaths")
    except Exception:  # noqa: BLE001 - no undo stack in this context (background mode): the bake itself is done
        log.debug("undo push unavailable", exc_info=True)
    return len(outputs), plan.estimate


def keep(scene) -> int:
    """Hand the scene's baked objects over to the user: remove the add-on's tags so a later bake never replaces them.
    Returns how many objects were released."""
    n = 0
    for ob in bake_data.tagged_objects(scene.name):
        data = ob.data
        for idb in (ob, data):
            if idb is not None:
                for k in (bp.TAG, bp.TAG_SOURCE, bp.TAG_SCENE):
                    if k in idb:
                        del idb[k]
        n += 1
    runtime.tag_redraw()
    return n


# -- lifecycle ---------------------------------------------------------------------------------------------------

def _abort() -> None:
    job, _rt.job = _rt.job, None
    if job is not None and job.task is not None:
        timers.runner.cancel(job.task)


def _on_load_pre() -> None:
    """A file load drops a bake in flight (its datablocks belong to the old file) and the last messages."""
    _abort()
    _rt.messages.clear()
    _rt.estimates.clear()


def register() -> None:
    registry.register_classes(bake_pg.classes)
    bpy.types.WindowManager.slicewright_bake = bpy.props.PointerProperty(type=bake_pg.SLICEWRIGHT_PG_Bake)
    if _on_load_pre not in registry.state.load_pre_hooks:
        registry.state.load_pre_hooks.append(_on_load_pre)


def unregister() -> None:
    while _on_load_pre in registry.state.load_pre_hooks:
        registry.state.load_pre_hooks.remove(_on_load_pre)
    _abort()
    _rt.messages.clear()
    _rt.estimates.clear()
    if hasattr(bpy.types.WindowManager, bake_pg.WM_PROP):
        delattr(bpy.types.WindowManager, bake_pg.WM_PROP)
    registry.unregister_classes(bake_pg.classes)
