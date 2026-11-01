import gmsh 
import numpy as np 
import sys 
import os 
from pathlib import Path

def convert_to_mesh(job, infill_path,  mesh_size): 
    BASE_DIR = Path(__file__).resolve().parents[1]
    JOBS_DIR = BASE_DIR / "jobs"

    STEP_path = JOBS_DIR / f"job_{job}" / "infills" / f"{infill_path}.step"
    MSH_path = JOBS_DIR / f"job_{job}" / "meshs" / f"{infill_path}.msh"

    STEP_path = f"./jobs/job_{job}/infills/{infill_path}.step" 
    if not os.path.exists(str(MSH_path)): 
        gmsh.initialize() 
        gmsh.option.setNumber("General.Terminal", 1) 
        gmsh.open(str(STEP_path))

        # mesh_size is the largest element allowed. Elements may be smaller where the
        # geometry needs it (thin rods and walls); forcing a minimum size equal to
        # mesh_size made elements coarser than the 0.45 mm rods they mesh.
        gmsh.option.setNumber("Mesh.MeshSizeMin", 0.1 * mesh_size)
        gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size)
        gmsh.option.setNumber("Mesh.Algorithm3D", 1)  # Delaunay: fastest and most robust for these lattices
        gmsh.model.mesh.generate(3) 

        # Sfepy is only compatible with MSH v2.2 
        gmsh.option.setNumber("Mesh.MshFileVersion", 2.2) 
        gmsh.write(str(MSH_path)) 

        gmsh.finalize() 
    else: print("mesh file already created")