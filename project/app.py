import os
import json
import base64

import psycopg
from psycopg.rows import dict_row
from flask import Flask, request, jsonify, send_from_directory, Response

app = Flask(__name__)

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SITE_DIST_DIR = BASE_DIR / "site" / "dist"
SITE_ASSETS_DIR = SITE_DIST_DIR / "assets"

DATABASE_URL = os.environ["DATABASE_URL"]

WORKER_SECRET = os.environ.get("WORKER_SECRET", "")


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


#frontend
@app.route("/", methods=["GET"])
def index():
    return send_from_directory(SITE_DIST_DIR, "index.html")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/assets/<path:path>", methods=["GET"])
def assets(path):
    return send_from_directory(SITE_ASSETS_DIR, path)


#public API
@app.route("/jobs", methods=["GET"])
def get_jobs():
    with db() as conn:
        rows = conn.execute(
            "select id, status, metrics from jobs order by id asc"
        ).fetchall()

    jobs = [
        {
            "job": f"job_{r['id']}",
            "metrics": r["metrics"],
        }
        for r in rows
    ]
    return jsonify({"jobs": jobs})


@app.route("/jobs/<job_name>", methods=["GET"])
def get_job(job_name):
    job_id = _job_id_from_name(job_name)
    if job_id is None:
        return jsonify({"status": "not_found", "job": job_name, "metrics": None}), 404

    with db() as conn:
        row = conn.execute(
            "select id, status, error, metrics from jobs where id = %s",
            (job_id,),
        ).fetchone()

    if row is None:
        return jsonify({"status": "not_found", "job": job_name, "metrics": None}), 404

    return jsonify({
        "job": f"job_{row['id']}",
        "status": row["status"],
        "error": row["error"],
        "metrics": row["metrics"],
    })


@app.route("/run", methods=["POST"])
def run_simulation():
    try:
        step_file = request.files["step_file"]
        sim_space = json.loads(request.form["sim_space"])
        step_bytes = step_file.read()

        with db() as conn:
            row = conn.execute(
                """
                insert into jobs (status, sim_space, base_part)
                values ('queued', %s, %s)
                returning id
                """,
                (json.dumps(sim_space), step_bytes),
            ).fetchone()
            conn.commit()

        job_id = row["id"]

        _trigger_worker()

        return jsonify({
            "status": "queued",
            "job": f"job_{job_id}",
            "metrics": None,
        })

    except Exception as e:
        return jsonify({
            "status": "failed",
            "job": None,
            "error": str(e),
            "metrics": None,
        }), 500


@app.route("/jobs/<job_name>/infills/<step_file>", methods=["GET"])
def get_infill_stl_file(job_name, step_file):
    return _serve_artifact(job_name, step_file, want_kind="stl", as_download=False)


@app.route("/jobs/<job_name>/infills/<step_file>/download", methods=["GET"])
def download_infill_step_file(job_name, step_file):
    return _serve_artifact(job_name, step_file, want_kind="step", as_download=True)


#internal Modal API

def _check_worker_auth():
    return WORKER_SECRET and request.headers.get("X-Worker-Secret") == WORKER_SECRET


@app.route("/internal/claim", methods=["POST"])
def internal_claim():
    if not _check_worker_auth():
        return jsonify({"error": "unauthorized"}), 401

    with db() as conn:
        row = conn.execute(
            """
            update jobs
            set status = 'running', updated_at = now()
            where id = (
                select id from jobs
                where status = 'queued'
                order by id asc
                for update skip locked
                limit 1
            )
            returning id, sim_space
            """
        ).fetchone()
        conn.commit()

    if row is None:
        return jsonify({"job": None})

    with db() as conn:
        part = conn.execute(
            "select base_part from jobs where id = %s", (row["id"],)
        ).fetchone()

    return jsonify({
        "job_id": row["id"],
        "sim_space": row["sim_space"],
        "base_part_b64": base64.b64encode(bytes(part["base_part"])).decode(),
    })

def _trigger_worker():
    
    url = os.environ.get("MODAL_TRIGGER_URL")
    if not url:
        return
    try:
        import urllib.request
        req = urllib.request.Request(url, data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  

@app.route("/internal/jobs/<int:job_id>/complete", methods=["POST"])
def internal_complete(job_id):
    if not _check_worker_auth():
        return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(force=True)
    status = payload.get("status", "complete")
    metrics = payload.get("metrics")
    error = payload.get("error")
    artifacts = payload.get("artifacts", [])  # [{name, kind, data_b64}, ...]

    with db() as conn:
        conn.execute(
            """
            update jobs
            set status = %s, metrics = %s, error = %s, updated_at = now()
            where id = %s
            """,
            (status, json.dumps(metrics) if metrics is not None else None,
             error, job_id),
        )
        for a in artifacts:
            conn.execute(
                """
                insert into artifacts (job_id, name, kind, data)
                values (%s, %s, %s, %s)
                on conflict (job_id, name, kind) do update set data = excluded.data
                """,
                (job_id, a["name"], a["kind"],
                 base64.b64decode(a["data_b64"])),
            )
        conn.commit()

    return jsonify({"ok": True})

#helpers
def _job_id_from_name(job_name):
    # Accepts "job_12" or "12".
    try:
        if job_name.startswith("job_"):
            return int(job_name.split("_", 1)[1])
        return int(job_name)
    except (ValueError, IndexError):
        return None


def _serve_artifact(job_name, step_file, want_kind, as_download):
    job_id = _job_id_from_name(job_name)
    if job_id is None:
        return jsonify({"error": "bad job"}), 404

    name = step_file
    if name.endswith(".step"):
        name = name[:-5]
    elif name.endswith(".stl"):
        name = name[:-4]

    with db() as conn:
        row = conn.execute(
            "select data from artifacts where job_id = %s and name = %s and kind = %s",
            (job_id, name, want_kind),
        ).fetchone()

    if row is None:
        return jsonify({"error": "artifact not found"}), 404

    data = bytes(row["data"])
    if want_kind == "stl":
        mimetype = "model/stl"
    else:
        mimetype = "application/step"

    headers = {}
    if as_download:
        headers["Content-Disposition"] = f'attachment; filename="{name}.{want_kind}"'

    return Response(data, mimetype=mimetype, headers=headers)


@app.route("/<path:path>", methods=["GET"])
def react_fallback(path):
    return send_from_directory(SITE_DIST_DIR, "index.html")


if __name__ == "__main__":
    app.run(debug=True)