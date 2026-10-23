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

Each design is solved as a linear-elastic TPU part (E = 12 MPa, ν = 0.45) with the
picked faces clamped and the force applied as a uniform pressure on the picked
circle, pushing into the part.

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

Known limits: the model is linear (small deformation, linear material) and has no
contact with an object. Large-deformation neo-Hookean runs on the reference
gripper (linear tets, 0.6 mm) gave 20–50% more displacement at 5 N, by a different
amount for each infill, but with a pressure-patch load Newton stopped converging
somewhere between about 1.5 and 4 N, depending on the design and element order.
A displacement-controlled indenter or contact model is needed before adding them.
The way to tell whether any of this matters is to test printed grippers and
compare with `validate.py`.

