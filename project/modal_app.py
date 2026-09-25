import os
import io
import json
import base64
import shutil
import tempfile
import urllib.request
from pathlib import Path

import modal

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "build-essential", "cmake", "ninja-build",
        "libgl1", "libglu1-mesa", "libxrender1", "libxcursor1",
        "libxi6", "libxinerama1", "libxrandr2", "libxft2", "libsm6",
    )
    .pip_install(
        "cadquery",
        "gmsh",
        "sfepy",
        "numpy",
        "scipy",
        "sympy",
        "matplotlib",
        "fastapi[standard]"
    )
    .add_local_python_source("sim")
)

try:
    from fastapi import HTTPException, Request
except ImportError:  # only needed inside the container, where the image installs it
    HTTPException = Request = None

app = modal.App("infill-worker", image=image)

SECRETS = modal.Secret.from_name("infill-secrets")


def _http_post(url, payload, secret, timeout=120):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Worker-Secret": secret,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return json.loads(body) if body else {}




@app.function(
    secrets=[SECRETS],
    timeout=1800,        
    cpu=4.0,
    memory=8192,         
    max_containers=1,    
)
def process_jobs():
    base_url = os.environ["RENDER_BASE_URL"].rstrip("/")
    secret = os.environ["WORKER_SECRET"]

    from sim.sim import run_sims
    import cadquery as cq

    processed = 0

    while True:
        claim = _http_post(f"{base_url}/internal/claim", {}, secret)
        job_id = claim.get("job_id")

        if not job_id:
            print(f"Queue empty. Processed {processed} job(s) this run.")
            return processed

        print(f"Claimed job {job_id}")

        try:
            sim_space = claim["sim_space"]
            if isinstance(sim_space, str):
                sim_space = json.loads(sim_space)
            base_part = base64.b64decode(claim["base_part_b64"])

            metrics, artifacts = _run_one_job(job_id, sim_space, base_part, cq, run_sims)

            _http_post(
                f"{base_url}/internal/jobs/{job_id}/complete",
                {
                    "status": "complete",
                    "metrics": metrics,
                    "artifacts": artifacts,
                },
                secret,
            )
            print(f"Job {job_id} complete ({len(artifacts)} artifacts)")

        except Exception as e:
            import traceback
            traceback.print_exc()
            _http_post(
                f"{base_url}/internal/jobs/{job_id}/complete",
                {"status": "failed", "error": str(e)},
                secret,
            )
            print(f"Job {job_id} failed: {e}")

        processed += 1


def _run_one_job(job_id, sim_space, base_part_bytes, cq, run_sims):
    import sim.sim as sim_module

    base_dir = Path(sim_module.__file__).resolve().parents[1]
    jobs_dir = base_dir / "jobs"
    job_root = jobs_dir / f"job_{job_id}"
    infills_dir = job_root / "infills"
    meshs_dir = job_root / "meshs"

    old_cwd = os.getcwd()
    try:
        if job_root.exists():
            shutil.rmtree(job_root, ignore_errors=True)
        infills_dir.mkdir(parents=True, exist_ok=True)
        meshs_dir.mkdir(parents=True, exist_ok=True)

        (job_root / "base_part.step").write_bytes(base_part_bytes)


        os.chdir(base_dir)

        metrics = run_sims(job_id, sim_space)

        artifacts = []
        for step_path in sorted(infills_dir.glob("*.step")):
            name = step_path.stem
            artifacts.append({
                "name": name,
                "kind": "step",
                "data_b64": base64.b64encode(step_path.read_bytes()).decode(),
            })

            stl_path = infills_dir / f"{name}.stl"
            model = cq.importers.importStep(str(step_path))
            cq.exporters.export(model, str(stl_path))
            artifacts.append({
                "name": name,
                "kind": "stl",
                "data_b64": base64.b64encode(stl_path.read_bytes()).decode(),
            })

        return metrics, artifacts

    finally:
        os.chdir(old_cwd)
        shutil.rmtree(job_root, ignore_errors=True)


@app.function(secrets=[SECRETS])
@modal.fastapi_endpoint(method="POST")
def trigger():
    process_jobs.spawn()
    return {"status": "triggered"}


@app.function(secrets=[SECRETS], timeout=120)
@modal.fastapi_endpoint(method="POST")
def analyze_part(payload: dict, request: Request):
    """Faces + tessellation of an uploaded STEP ({"step_b64": ...}) for face picking."""
    if request.headers.get("X-Worker-Secret") != os.environ["WORKER_SECRET"]:
        raise HTTPException(status_code=401, detail="unauthorized")

    from sim.faces import load_step_bytes, analyze_part as analyze

    try:
        part = load_step_bytes(base64.b64decode(payload["step_b64"]))
        return analyze(part)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not analyze the STEP file: {e}")

