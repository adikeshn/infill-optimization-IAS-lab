# Infill Optimization IAS Lab

Software for generating, simulating, and ranking 3D-printable infill designs for compliant robotic grippers. The app takes a base STEP file, generates multiple infill-density variants, runs finite element analysis, and ranks the results by displacement and stress performance.

## What it does

- Upload a compliant gripper STEP file (any XZ cross-section, extruded along Y)
- Click the faces to fix and the point where a circular force is applied
- Select infill patterns and densities
- Generate a shell + infill geometry with CadQuery (the infill is exposed on the two Y faces)
- Mesh each design with Gmsh
- Run FEA with SfePy using the picked boundary conditions
- Rank designs using stress, displacement, and a pseudo-CGS score
- View/download generated STEP/STL results in the web app

## Supported infills

- Grid
- Honeycomb
- FinRay
- Triangle

## Tech stack

- Frontend: React, Vite, Three.js, React Three Fiber
- Backend: Flask, Gunicorn, PostgreSQL
- Worker: Modal
- CAD/FEA: CadQuery, Gmsh, SFePy, NumPy
- Deployment: Docker + Render

## Project structure

```text
project/
  app.py              # Flask API + job/artifact routes
  modal_app.py        # Modal worker for long-running simulations
  sim/
    gen.py            # Shell + infill geometry generation
    faces.py          # Face analysis for picking + picked faces -> FEA regions
    gmsh.py           # STEP to mesh conversion
    sfepy.py          # SFePy setup and FEA solve
    sim.py            # Batch simulation + ranking logic
  run_local.py        # Run a job without Modal/Postgres
  validate.py         # Compare a job's ranking against physical test results
  site/
    src/App.jsx       # React UI
Dockerfile            # Builds frontend and runs Flask app
render.yaml           # Render deployment config

## FEA model and metrics

Each design is solved as a TPU part (E = 12 MPa, ν = 0.45 by default, both editable
in the Material panel) with the picked faces clamped and the force applied as a dead
load on the picked circle - a fixed direction and magnitude integrated over the
*undeformed* surface, pushing into the part. By default the solve is linear-elastic;
the "Hyperelastic (large deformation)" toggle switches to a nearly-incompressible
Neo-Hookean, Total Lagrangian solve instead (`dw_tl_he_neohook` + `dw_tl_bulk_penalty`
in `sim/sfepy.py`), with `mu`/`K` calibrated from the same (E, ν) so the two models
agree at small strain. The dead-load traction term doesn't depend on displacement, so
it's reused unchanged by both paths - this is deliberately *not* a follower/pressure
load, which is harder to converge (see "Known limits" below).

- **Elements:** quadratic (10-node) tetrahedra. Linear tetrahedra are far too stiff
  in bending: on the reference gripper they underestimated displacement by 1.3–2.5×,
  by a different factor for each infill pattern.
- **Mesh size** is the largest element allowed; elements shrink where thin rods and
  walls need it. On the reference gripper, 1.0 mm is within ~3% of a converged
  mesh and 0.6 mm within ~1%.
- **Displacement** is the load-point displacement: the force patch's average
  displacement along the force direction.
- **Stress** is the volume-weighted mean von Mises stress between the 90th and 95th
  percentiles. The top 5% is excluded because stress at clamp edges, sharp
  corners and the edge of the force patch grows without limit as the mesh is
  refined.
- **Pseudo-CGS** = (normalized displacement + (1 − normalized stress)) / 2, with each
  metric min-max normalized across the job. Scores are only comparable within a job.

Known limits: by default the model is linear (small deformation, linear material) and
has no contact with an object. The hyperelastic toggle addresses the first part, but
not fully: earlier large-deformation neo-Hookean runs on the reference gripper (linear
tets, 0.6 mm, a pressure-patch follower load) gave 20–50% more displacement at 5 N than
the linear model, by a different amount for each infill, but Newton stopped converging
somewhere between about 1.5 and 4 N, depending on the design and element order - right
around this app's default force magnitude. The current hyperelastic path uses a dead
load instead of a follower load (see above), which should converge more easily, but
this hasn't been re-measured against those numbers; `calc_gripper_results` raises a
clear error rather than returning an under-converged result if Newton doesn't converge,
so a job failing with that error at large forces is expected, not a bug. Neither path
has contact with an object - a displacement-controlled indenter or contact model would
be needed for that. The way to tell whether any of this matters is to test printed
grippers and compare with `validate.py`.

