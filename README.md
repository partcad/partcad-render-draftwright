# partcad-render-draftwright

A PartCAD package that produces **technical drawings** — orthographic views,
dimensions, section views with ISO hatching, and a title block — for any
PartCAD part or assembly, as **PDF**, **SVG** or **DXF**.

The drawing is produced by [draftwright](https://github.com/pzfreo/draftwright).
This package is the PartCAD side of it: it declares the three file types and
the script PartCAD runs to write them. No draftwright code is vendored here —
PartCAD installs draftwright from PyPI into the sandbox it runs the
implementation in.

Published in the PartCAD index as `//pub/feature/render/draftwright`.

## What it changes

Out of the box, PartCAD's `svg` and `dxf` are flat projections of the shape,
and there is no `pdf` at all. With this package:

| file type | without it | with it |
|---|---|---|
| `pdf` | not implemented | a full technical drawing |
| `svg` | a 2D projection | a full technical drawing |
| `dxf` | the projection, converted | a full technical drawing |

## Why these are `render:` types and not `export:` ones

PartCAD's two output sections are not two names for the same thing:

- **`export:`** is for files another CAD tool opens as a **part or a sketch** —
  geometry it can go on working with. STEP, BREP, STL, 3MF.
- **`render:`** is for **output files in general** — a drawing, a picture, a
  report. SVG, PNG, DXF.

A technical drawing is a *document about* a part, not the part. Dimensioned
views, a title block and a section arrow are for a person to read; nothing
downstream can load the PDF and machine from it. So all three file types here
are declared under `render:`.

Nothing is lost by that. PartCAD reads `export:` as a fallback when a `render:`
file type has no implementation — an export file is a stricter thing than an
output file, so it also serves wherever any output file would do — and the
section a file type belongs to is decided by **where it is declared, not by
which command asked for it**. Both of these produce the drawing:

```bash
pc render -t pdf -e //pub/feature/render/draftwright //your/package:bracket
pc export -t pdf -e //pub/feature/render/draftwright //your/package:bracket
```

What the two sections settle is not reachability but meaning. Declaring a file
type under `export:` promises callers geometry another tool can go on working
with; declaring it under `render:` promises an output file, nothing more. A
drawing is the latter, so `export:` would promise a part this package cannot
deliver — which is the mistake the split exists to prevent.

## Using it

### For one command, in any package

```bash
pc render -t pdf -e //pub/feature/render/draftwright //your/package:bracket
pc render -t svg -e //pub/feature/render/draftwright //your/package:bracket
pc render -t dxf -e //pub/feature/render/draftwright //your/package:bracket
```

`-e` / `--options-package` tells PartCAD to read the output configuration from
this package in addition to the object's own, so nothing has to change in the
package being drawn.

### For every object in your package

Import it, and declare the file types you want:

```yaml
import:
  draftwright:
    type: git
    url: https://github.com/partcad/partcad-render-draftwright

render:
  pdf:
    package: //pub/feature/render/draftwright
    path: render_draftwright.py
  svg:
    package: //pub/feature/render/draftwright
    path: render_draftwright.py
```

## Drawing options

Every field of a file type that PartCAD does not reserve is handed to the
implementation, so draftwright's options are set in `partcad.yaml` — per
package, or per object to override the package:

```yaml
render:
  pdf:
    title: Mounting Plate      # default: the object's name
    number: DWG-001
    tolerance: ISO 2768-m
    drawn_by: A. Engineer
    company: ACME Corp.
    material: AL 6061-T6
    revision: A
    scale: 2                   # 2:1; default: chosen to fit the page
    page: A3                   # or "WIDTHxHEIGHT" in mm
    projection: first          # or 'third'
    auto_dims: true            # false to place your own dimensions
    pmi: annotate              # use the AP242 PMI carried by the model
```

An option left out keeps draftwright's own default rather than one invented
here. See [draftwright's documentation](https://github.com/pzfreo/draftwright)
for what each of them means.

## Requirements

- PartCAD with support for package-defined output implementations (the `path`
  field of a `render:` / `export:` file type, and `-e` / `--options-package`).
- Nothing to install by hand.

draftwright is declared as this package's Python requirement:

```yaml
pythonRequirements:
  - draftwright==0.4.3
```

PartCAD installs a package's requirements into the sandbox interpreter it runs
that package's implementations in, before running any of them, so the runtime
environment gets draftwright from PyPI and no copy of it lives here. It is
declared once for the package rather than on each of the three file types
because one implementation serves all three.

Only draftwright is named. It resolves its own CAD stack on purpose — its
dependency markers pick the build123d/OCP pair that matches the sandbox
interpreter, and pinning PartCAD's own pair on top would fight that resolution
on the 3.11 sandbox, where draftwright requires `build123d<0.11`. The shape
crosses the sandbox boundary as BREP, which does not care which OCP build is on
the other side.

## Licensing

This package is licensed under the **Apache License 2.0** (see
[LICENSE](LICENSE)), like PartCAD itself. It contains no draftwright code.

draftwright, which this package installs and calls, is licensed under the
**AGPL-3.0**. Using this package means installing it; if that does not suit
your project, PartCAD's built-in `svg`, `dxf` and `png` remain available and
are unaffected unless you ask for this package by name.
