from sim.util import minmax
from sim.gmsh import convert_to_mesh
from sim.gen import get_finray_infill, get_grid_infill, get_honeycomb_infill, get_triangle_infill
from sim.sfepy import load_Domain_sfepy, generate_regions, calc_gripper_results
from sim.util import computePseudoCGS
from sim.faces import resolve_bcs
from pathlib import Path
import matplotlib.pyplot as plt
import cadquery as cq
from cadquery import exporters
import numpy as np
import os



def model_cad(job, step_path, mesh_size, bcs):

    convert_to_mesh(job, step_path, mesh_size)

    domain, omega = load_Domain_sfepy(job, step_path)

    regions_dict = generate_regions(domain, bcs)

    stress, disp = calc_gripper_results(omega, regions_dict, bcs)
    von_mises, disp = computePseudoCGS(disp, stress)
    von_mises = np.asarray(von_mises).ravel()
    von_mises = von_mises[np.isfinite(von_mises)]


    p95 = np.percentile(von_mises, 95)
    p90 = np.percentile(von_mises, 90)
    p80 = np.percentile(von_mises, 80)

    top_5_20_band = von_mises[(von_mises >= p90) & (von_mises <= p95)]
    return [np.mean(top_5_20_band), max(np.linalg.norm(disp, axis=1))]
    

def _fmt_num(x):
    x = round(float(x), 2)
    if x == int(x):
        x = int(x)
    return str(x).replace("-", "n").replace(".", "p")


def _design_name(infill_type, den, angle):
    return f"{infill_type}-{_fmt_num(den)}-{_fmt_num(angle)}deg"


def get_metrics(job, part, infill_type, den, angle, mesh_size, outline_thickness, infill_thickness, JOBS_DIR, bcs):

    step_file_name = _design_name(infill_type, den, angle)

    if infill_type == "finr":
        infill, density = get_finray_infill(part, density = den,
                        rod_diameter = infill_thickness,
                        outline_thickness=outline_thickness,
                        angle_deg=angle)
    elif infill_type == "honey":
        infill, density = get_honeycomb_infill(part, density=den,
                        rod_diameter = infill_thickness,
                        outline_thickness=outline_thickness,
                        angle_deg=angle)
    elif infill_type == "grid":
        infill, density = get_grid_infill(part, density = den,
                        rod_diameter = infill_thickness,
                        outline_thickness=outline_thickness,
                        angle_deg=angle)
    elif infill_type == "tri":
        infill, density = get_triangle_infill(part, density = den,
                        rod_diameter = infill_thickness,
                        outline_thickness=outline_thickness,
                        angle_deg=angle)
    else:
        raise ValueError(f"Unknown infill type {infill_type!r}.")

    exporters.export(infill, f"{JOBS_DIR}/job_{job}/infills/{step_file_name}.step")

    return model_cad(job, step_file_name, mesh_size, bcs), density



def run_sims(job, sim_space):
    BASE_DIR = Path(__file__).resolve().parents[1]
    JOBS_DIR = BASE_DIR / "jobs"

    cgs, stress, disp, names = [], [], [], []
    part = cq.importers.importStep(str(JOBS_DIR / f"job_{job}" / "base_part.step"))
    bcs = resolve_bcs(part, sim_space)
    for entry in sim_space["infills"]:
        infill_type = entry["type"]
        den = entry["density"]
        angle = entry.get("angle", 0)
        names.append(_design_name(infill_type, den, angle))
        mets, density = get_metrics(job, part, infill_type, den, angle,
                                    sim_space["mesh_size"], sim_space["out_thickness"],
                                    sim_space["inf_thickness"], str(JOBS_DIR), bcs)
        stress.append(mets[0])
        disp.append(mets[1])
    minmax_stress = minmax(stress)
    minmax_disp = minmax(disp)
    for i in range(len(names)):
        cgs.append((minmax_disp[i] + (1-minmax_stress[i]))/2)

    sorted_with_index = (sorted(enumerate(cgs), key=lambda x: x[1], reverse=True))
    return [[names[index], pseudo, disp[index], stress[index]] for (index, pseudo) in sorted_with_index]
