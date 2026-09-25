"""Run a job locally, without Modal or Postgres, and print the ranking.

usage: python run_local.py <part.step> <sim_space.json> [job_name]

sim_space is the JSON the web app submits, e.g. for experiments/GripperForOpt_v2.step
with the boundary conditions the app used to hardcode:

{"mesh_size": 1.0, "out_thickness": 0.87, "inf_thickness": 0.45,
 "infills": [{"type": "grid", "density": 20, "angle": 45}],
 "fixed_faces": [{"index": 2, "centroid": [-9.4, 2.5, 0.0]},
                 {"index": 1, "centroid": [-9.4, 2.5, 18.3535]}],
 "force": {"face": {"index": 0, "centroid": [0.0, 2.5, 18.3535]},
           "center": [0.0, 2.5, 18.3535], "diameter": 5.2, "magnitude": 5.0}}

Face indices and centroids come from sim.faces.analyze_part (what /analyze returns).
Outputs are left in jobs/job_<job_name>/.
"""
import json
import os
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def main(step_path, sim_space_path, job="local"):
    sim_space = json.loads(Path(sim_space_path).read_text())
    job_root = BASE_DIR / "jobs" / f"job_{job}"
    shutil.rmtree(job_root, ignore_errors=True)
    (job_root / "infills").mkdir(parents=True)
    (job_root / "meshs").mkdir(parents=True)
    shutil.copy(step_path, job_root / "base_part.step")

    os.chdir(BASE_DIR)
    from sim.sim import run_sims

    for name, cgs, disp, stress in run_sims(job, sim_space):
        print(f"{name:24s} pseudoCGS={cgs:.4f} disp={disp:.5f} stress={stress:.5f}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
