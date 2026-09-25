"""B-rep face analysis for the face-picking UI, and resolution of the picked
faces into the geometry the FEA boundary conditions are built from."""
import os
import tempfile

import cadquery as cq
import numpy as np
from scipy.spatial import cKDTree

from sim.gen import cross_section_face, part_bbox


def load_step_bytes(data):
    fd, path = tempfile.mkstemp(suffix=".step")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return cq.importers.importStep(path)
    finally:
        os.remove(path)


def _diag(part):
    return part_bbox(part).DiagonalLength


def analyze_part(part):
    """Faces (index = position in part.faces().vals()) and a tessellation for picking."""
    cross_section_face(part)  # reject non-prismatic parts before the user starts picking

    tol = 1e-3 * _diag(part)
    faces, positions, triangles, face_ids = [], [], [], []
    for i, face in enumerate(part.faces().vals()):
        planar = face.geomType() == "PLANE"
        faces.append({
            "index": i,
            "type": face.geomType(),
            "area": round(face.Area(), 6),
            "centroid": [round(c, 6) for c in face.Center().toTuple()],
            "normal": [round(c, 6) for c in face.normalAt().toTuple()] if planar else None,
        })

        verts, tris = face.tessellate(tol, 0.2)
        offset = len(positions) // 3
        positions += [round(c, 4) for v in verts for c in v.toTuple()]
        triangles += [offset + j for t in tris for j in t]
        face_ids += [i] * len(tris)

    return {
        "faces": faces,
        "tessellation": {"positions": positions, "triangles": triangles, "face_ids": face_ids},
    }


def _resolve_face(faces, ref, tol, what):
    """The face a {"index", "centroid"} reference from the UI points at."""
    try:
        index = int(ref["index"])
        centroid = np.array(ref["centroid"], dtype=float)
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"The {what} is malformed: expected {{'index': int, 'centroid': [x, y, z]}}, got {ref!r}.")

    centers = np.array([f.Center().toTuple() for f in faces])
    dist = np.linalg.norm(centers - centroid, axis=1)
    if 0 <= index < len(faces) and dist[index] <= tol:
        return index
    matches = np.flatnonzero(dist <= tol)
    if len(matches) == 1:
        return int(matches[0])
    raise ValueError(
        f"The {what} (face #{index}, centroid {centroid.round(3).tolist()}) could not be "
        "found on the uploaded part. Re-upload the STEP file and pick the faces again."
    )


def _face_triangles(face, tol):
    verts, tris = face.tessellate(tol, 0.1)
    return np.array([v.toTuple() for v in verts])[np.array(tris)]


def _inward_normal(solid, face, point, eps):
    n = face.normalAt(cq.Vector(*point))
    p = cq.Vector(*point)
    ahead, behind = solid.isInside(p + n * eps), solid.isInside(p - n * eps)
    if behind and not ahead:
        return -np.array(n.toTuple())
    if ahead and not behind:
        return np.array(n.toTuple())
    raise ValueError(
        "Could not tell which side of the force face is material at the picked point. "
        "Pick a point further from the face's edges."
    )


def resolve_bcs(part, sim_space):
    """Turn the faces picked in the UI into geometry for vertex classification.

    The FEA runs on the infilled solid, whose face IDs differ from the original
    part's, so the picked faces are carried as fine tessellations of the original
    faces and mesh vertices are matched against them geometrically (the shell
    keeps the original outer surface).
    """
    fixed_refs = sim_space.get("fixed_faces")
    force = sim_space.get("force")
    if not fixed_refs:
        raise ValueError("This job has no fixed faces. Select at least one fixed face in the UI and resubmit.")
    if not force:
        raise ValueError("This job has no force. Pick a force face and point in the UI and resubmit.")

    solid = part.val()
    faces = part.faces().vals()
    diag = _diag(part)
    tol = min(1e-3 * diag, 0.05)
    tess_tol = tol / 4

    fixed = [_resolve_face(faces, ref, 1e-4 * diag, "fixed face") for ref in fixed_refs]
    force_index = _resolve_face(faces, force.get("face"), 1e-4 * diag, "force face")
    if force_index in fixed:
        raise ValueError(f"Face #{force_index} is selected as both a fixed face and the force face.")

    try:
        center = np.array(force["center"], dtype=float).reshape(3)
        radius = float(force["diameter"]) / 2
        magnitude = float(force["magnitude"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("The force needs a center [x, y, z], a diameter and a magnitude.")
    if radius <= 0 or magnitude <= 0:
        raise ValueError("The force diameter and magnitude must be positive.")

    force_face = faces[force_index]
    force_tris = _face_triangles(force_face, tess_tol)
    if not points_near_triangles(center[None], force_tris, 1e-2 * diag)[0]:
        raise ValueError(f"The force center {center.round(3).tolist()} is not on face #{force_index}.")
    if force_face.geomType() == "PLANE":
        n = np.array(force_face.normalAt().toTuple())
        center = center - np.dot(center - np.array(force_face.Center().toTuple()), n) * n

    return {
        "tol": tol,
        "fixed": [{"index": i, "tris": _face_triangles(faces[i], tess_tol)} for i in fixed],
        "force": {
            "index": force_index,
            "tris": force_tris,
            "center": center,
            "radius": radius,
            "magnitude": magnitude,
            "direction": _inward_normal(solid, force_face, center, 2 * tol),
        },
    }


def _segment_distance(p, a, b):
    ab = b - a
    t = np.einsum("ij,ij->i", p - a, ab) / np.maximum(np.einsum("ij,ij->i", ab, ab), 1e-300)
    return np.linalg.norm(p - (a + np.clip(t, 0, 1)[:, None] * ab), axis=1)


def _point_triangle_distance(p, tri):
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    n = np.cross(b - a, c - a)
    length = np.linalg.norm(n, axis=1)
    inside = length > 1e-12  # degenerate triangles fall back to edge distances
    n = n / np.where(inside, length, 1.0)[:, None]
    h = np.einsum("ij,ij->i", p - a, n)
    q = p - h[:, None] * n
    for u, v in ((a, b), (b, c), (c, a)):
        inside &= np.einsum("ij,ij->i", np.cross(v - u, q - u), n) >= 0
    edges = np.min([_segment_distance(p, u, v) for u, v in ((a, b), (b, c), (c, a))], axis=0)
    return np.where(inside, np.abs(h), edges)


def points_near_triangles(points, tris, tol, chunk=200_000):
    """Mask of points within `tol` of a triangle soup of shape (T, 3, 3)."""
    mask = np.zeros(len(points), dtype=bool)
    if len(points) == 0 or len(tris) == 0:
        return mask
    centroids = tris.mean(axis=1)
    radii = np.linalg.norm(tris - centroids[:, None], axis=2).max(axis=1)
    hits = cKDTree(points).query_ball_point(centroids, radii + tol)
    tri_idx = np.repeat(np.arange(len(tris)), [len(h) for h in hits])
    pt_idx = np.concatenate([np.asarray(h, dtype=int) for h in hits])
    for s in range(0, len(pt_idx), chunk):
        pi, ti = pt_idx[s:s + chunk], tri_idx[s:s + chunk]
        mask[pi[_point_triangle_distance(points[pi], tris[ti]) <= tol]] = True
    return mask
