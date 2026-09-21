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
way instead - by returning {"success": False, "exception": ...}, and anything
worth saying about a file that was still produced correctly by returning
{"warnings": [...]}, which PartCAD logs against the object.

What draftwright thinks of the drawing is reported through those two. It lints
every sheet it builds and writes the result to its own logger; PartCAD relays a
sandbox's stderr as one warning line, so an error there - a dimension that could
not be placed, a label over a centreline - reached the user as log noise
attached to a render that had reported success. 'process()' asks for the same
critique through 'Drawing.lint()' and fails the render on anything draftwright
calls an error, so 'pc render' exits non-zero instead of leaving behind a
drawing that is quietly missing a dimension.

Whether a file is asked for reproducibly is the caller's to say, and it arrives
in 'request["reproducible"]'. PartCAD puts that in every request it sends,
whether or not the file type declared it, so it is read without a default of our
own: a package writes

    render:
      pdf:
        reproducible: true

and it reaches here. That it is the same word on both sides is not a
coincidence - draftwright had 'reproducible=' first, and PartCAD took the name
for the protocol field precisely so that a package setting it once settles both
ends.

What it settles on this side: draftwright's 'reproducible=' fixes the order of
the elements it writes and pins the metadata its exporters otherwise take from
the clock - reportlab's /CreationDate and /ID in the PDF, ezdxf's $TDCREATE and
the GUID pair in the DXF - so two renders of an object that has not changed are
byte-identical. That is what makes a drawing worth keeping in a repository next
to the model: 'git diff' answers whether the drawing changed, and a checksum
answers it without opening the file.

It is not free - draftwright measures the ordering at about a third of DXF
export time again - which is why it is off unless asked for, matching PartCAD's
own default. A drawing produced to be looked at and thrown away should not pay
for it; one that is kept says so.

draftwright refuses rather than degrades when it cannot produce a reproducible
file, which is the right way round: it arrives here as a failed render instead
of a file quietly stamped with the run that made it.

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


def _reproducible(request):
    """Whether this file has to come out the same every time it is written.

    PartCAD's protocol field, read the way PartCAD reads it: a real boolean from
    a 'partcad.yaml', and the name of one from a caller that is not YAML -- the
    CLI and the JSON-RPC clients hand values through as they parsed them, and a
    'reproducible' that arrived as the string "false" and was taken for true
    would turn a guarantee off without saying so.

    Not one of DRAWING_PARAMETERS: those are passed to 'build_drawing()'
    untouched and this one is passed to 'export()' as well.
    """
    value = request.get("reproducible")
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "on", "1")
    return bool(value)


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


def _critique(drawing):
    """What draftwright says about the sheet it just built.

    Returns '(errors, advisories)', each a list of lines. An 'error' severity is
    draftwright refusing to stand behind the drawing - most often a measurement
    it could not place, like the overall width when both dimension strips of a
    view are full ('overall_dim_withheld'). That is not a warning: a drawing
    missing an overall dimension is not a drawing anyone can work from.

    Linting costs a second pass over an already-built sheet. It is asked for
    rather than scraped out of draftwright's log because the log line is prose
    and this is a decision.
    """
    try:
        issues = drawing.lint()
    except Exception as e:
        # A critique that cannot be produced is not a failed drawing. Say so and
        # let the file stand.
        return [], ["draftwright could not lint the drawing: %s" % e]

    errors, advisories = [], []
    for issue in issues:
        line = "[%s] %s: %s" % (issue.severity, issue.code, issue.message)
        suggestion = getattr(issue, "suggestion", None)
        if suggestion:
            line += " -- %s" % suggestion
        (errors if issue.severity == "error" else advisories).append(line)
    return errors, advisories


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
        # What the caller asked for, at both the seams draftwright offers it:
        # 'build_drawing()' sets the drawing's own default and carries it through
        # the repack that rebuilds the sheet, 'export()' settles the file
        # actually being written. Both, because either alone leaves half of it:
        # the element order comes from the first and the file metadata from the
        # second.
        reproducible = _reproducible(request)
        drawing = build_drawing(_shape(request["wrapped"]), out=stem, reproducible=reproducible, **_parameters(request))
        produced = drawing.export(stem, formats=(file_format,), reproducible=reproducible)

        # The dict form is what 'formats=' returns. The tuple is the deprecated
        # shape 'export()' falls back to when asked for no formats at all, which
        # this never does; handled rather than assumed away.
        written = produced.get(file_format) if isinstance(produced, dict) else path
        if written and os.path.abspath(written) != os.path.abspath(path):
            os.replace(written, path)

        if not os.path.exists(path) or os.path.getsize(path) == 0:
            raise Exception("draftwright produced no %s file: %s" % (file_format.upper(), path))

        # Judged after the file is written rather than before, so that a drawing
        # draftwright will not stand behind is still there to be looked at. The
        # render fails all the same.
        errors, advisories = _critique(drawing)
        if errors:
            return {
                "success": False,
                "exception": "draftwright reports %d error(s) in the drawing (written to %s so it can be "
                "inspected):\n  %s" % (len(errors), os.path.basename(path), "\n  ".join(errors)),
                "warnings": advisories,
            }

        return {"success": True, "exception": None, "warnings": advisories}

    except Exception as e:
        return {"success": False, "exception": "".join(traceback.format_exception(type(e), e, e.__traceback__))}
