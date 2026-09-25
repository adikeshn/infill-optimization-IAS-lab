"""B-rep face analysis for the face-picking UI."""
import os
import tempfile

import cadquery as cq

from sim.gen import cross_section_face


def load_step_bytes(data):
    fd, path = tempfile.mkstemp(suffix=".step")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return cq.importers.importStep(path)
    finally:
        os.remove(path)


def _diag(part):
    return part.val().BoundingBox().DiagonalLength


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
