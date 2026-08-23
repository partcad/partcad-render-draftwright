#
# PartCAD render implementation backed by draftwright.
#
# Licensed under Apache License, Version 2.0.
#
"""Produce a technical drawing of a shape with draftwright.

The three file types this implements are 'render:' types, not 'export:' ones.
A drawing is a document about a part rather than a part: nothing downstream can
open it as geometry and carry on working, which is what 'export:' promises. See
'partcad.yaml' for what that distinction buys and costs.

This runs inside a PartCAD sandbox, driven by PartCAD's output meta-wrapper
('wrappers/wrapper_export.py' - one wrapper serves both sections). The wrapper
hands it two globals and calls 'process()':

    request  -- the shape in 'request["wrapped"]' (an OCP 'TopoDS_Shape'),
                every parameter declared for this file type in 'partcad.yaml',
                and 'shape_name' / 'shape_kind' / 'shape_type'
    path     -- the absolute path of the file to write

Unlike the implementations that ship inside PartCAD, nothing here imports
'wrapper_common': that module is a PartCAD internal, and a package published on
its own should not be pinned to its shape. Failures are reported the documented
way instead - by returning {"success": False, "exception": ...}.

draftwright derives the whole drawing from the solid: orthographic views,
dimensions, a section view, and a title block. PartCAD only says which file to
write and with what parameters.
"""

import os
import traceback

import build123d as b3d
from draftwright import build_drawing

# What this package implements. draftwright can also write PNG; that is left to
# PartCAD's built-in rasterizer, which is what a 'png' render already means.
FORMATS = ("pdf", "svg", "dxf")

# The parameters passed straight through to 'build_drawing()'. Anything absent
# from the configuration keeps draftwright's own default rather than being
# second-guessed here, so this list is names only - no defaults of our own.
DRAWING_PARAMETERS = (
    "number",
    "tolerance",
    "drawn_by",
    "scale",
    "page",
    "auto_dims",
    "detail_view",
    "pmi",
    "assembly",
    "material",
    "date",
    "revision",
    "company",
    "frame",
    "projection",
    "zones",
)


def _shape(wrapped):
    """The shape PartCAD sent, as something build123d can hand to draftwright.

    Borrowed the way PartCAD's own renderers do it (see the built-in
    '//builtin/render/render_svg.py'): take any Shape instance and replace what
    it wraps. Deliberately not 'b3d.Shape.cast()', which reads like the honest
    downcast but is an abstract method with an empty body on the base class -
    on build123d 0.11 it answers None instead of raising, so a 'try/except'
    around it never fires and every drawing dies inside draftwright with
    'TypeError: expected str, bytes or os.PathLike object, not NoneType'.

    Nothing here needs the precise build123d class anyway: draftwright checks
    that it was handed a Shape and reads the geometry off '.wrapped'.
    """
    shape = b3d.Solid.make_box(1, 1, 1)
    shape.wrapped = wrapped
    return shape


def _format_of(path):
    """Which of the three file types the output path asks for."""
    extension = os.path.splitext(path)[1].lstrip(".").lower()
    if extension not in FORMATS:
        raise Exception(
            "draftwright writes %s, not '%s' (from %s)" % (", ".join(FORMATS).upper(), extension, os.path.basename(path))
        )
    return extension


def _parameters(request):
    """The 'build_drawing()' keyword arguments this request asks for."""
    parameters = {name: request[name] for name in DRAWING_PARAMETERS if request.get(name) is not None}
    # The title block should say what the object is called unless the package
    # said otherwise. draftwright would otherwise title it after the file name,
    # which for PartCAD output is the object name uppercased anyway - but only
    # by coincidence of how the file is named.
    title = request.get("title") or request.get("shape_name")
    if title:
        parameters["title"] = title
    return parameters


def process(path, request):
    try:
        file_format = _format_of(path)
        if request.get("shape_kind") == "sketch":
            raise Exception("draftwright draws solids; '%s' is a sketch" % request.get("shape_name"))

        # 'export()' appends the format's own extension to the stem it is
        # given, and strips one it recognizes, so handing it the output path
        # without its extension makes it write exactly the file PartCAD asked
        # for.
        stem = os.path.splitext(path)[0]
        drawing = build_drawing(_shape(request["wrapped"]), out=stem, **_parameters(request))
        produced = drawing.export(stem, formats=(file_format,))

        # The dict form is what 'formats=' returns; be tolerant of the legacy
        # tuple in case an older draftwright is installed.
        written = produced.get(file_format) if isinstance(produced, dict) else path
        if written and os.path.abspath(written) != os.path.abspath(path):
            os.replace(written, path)

        if not os.path.exists(path) or os.path.getsize(path) == 0:
            raise Exception("draftwright produced no %s file: %s" % (file_format.upper(), path))

        return {"success": True, "exception": None}

    except Exception as e:
        return {"success": False, "exception": "".join(traceback.format_exception(type(e), e, e.__traceback__))}
