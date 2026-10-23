
import numpy as np
from sfepy.discrete.fem import Mesh, Field
from sfepy.discrete import (Material, FieldVariable, Integral, Equation, Equations, Problem)
from sfepy.terms import Term
from sfepy.discrete import Problem
from sfepy.mechanics.matcoefs import stiffness_from_youngpoisson
from sfepy.discrete.fem import FEDomain
from sfepy.discrete.conditions import EssentialBC, Conditions
from sfepy.solvers.ls import ScipyDirect
try:  # MKL Pardiso is ~6x faster than SuperLU on these systems; fall back if MKL is missing
    import pypardiso  # noqa: F401
    from sfepy.solvers.ls import PyPardisoSolver as LinearSolver
except Exception:
    LinearSolver = ScipyDirect
from sfepy.solvers.nls import Newton 
from sfepy.base.base import IndexedStruct
from sim.faces import points_near_triangles
from sim.util import cauchy_to_von_mises
from sfepy import data_dir


def load_Domain_sfepy(job, mesh_filename):

    mesh = Mesh.from_file(f"./jobs/job_{job}/meshs/{mesh_filename}.msh")
    domain = FEDomain('domain', mesh)
    omega = domain.create_region('Omega', 'all')
    return domain, omega

#Boundary regions from the faces picked in the UI (see sim.faces.resolve_bcs): the
#fixed faces are clamped and the force is applied on the force face inside the circle
def generate_regions(domain, bcs):
    coors = domain.mesh.coors
    surface = domain.create_region('Gamma_surface', 'vertices of surface', 'facet').vertices
    tol = bcs["tol"]

    def on_face(tris):
        return surface[points_near_triangles(coors[surface], tris, tol)]

    def facet_region(name, vertices):
        return domain.create_region(name, f'vertices by {name}_vertices', 'facet',
                                    functions={f'{name}_vertices': lambda coors, domain=None: vertices},
                                    allow_empty=True)

    fixed = []
    for k, face in enumerate(bcs["fixed"]):
        region = facet_region(f'Gamma_fixed_{k}', on_face(face["tris"]))
        if region.facets.size == 0:
            raise ValueError(f"Fixed face #{face['index']} picks up no mesh facets. Try a finer mesh size.")
        fixed.append(region)

    force = bcs["force"]
    vertices = on_face(force["tris"])
    vertices = vertices[np.linalg.norm(coors[vertices] - force["center"], axis=1) <= force["radius"]]
    force_region = facet_region('Gamma_force', vertices)
    if force_region.facets.size == 0:
        raise ValueError(
            f"The {2 * force['radius']:g} mm force circle on face #{force['index']} picks up no mesh "
            "facets. Use a larger force diameter or a finer mesh size."
        )

    return {"fixed": fixed, "force": force_region}


#Quadratic (10-node) tetrahedra. Linear tets are far too stiff in bending: on the reference
#gripper they underestimated displacement by 1.3-2.5x depending on the infill pattern, which
#is enough to reorder patterns. With quadratic tets, results change <3% between 1.0 and 0.45 mm.
APPROX_ORDER = 2


def calc_gripper_results(omega, regions, bcs):
    """Linear-elastic solve. Returns (von Mises stress at every quadrature point [MPa],
    the volume each quadrature point represents [mm^3], and the load-point displacement:
    the area-averaged displacement of the force patch along the force direction [mm])."""

    field = Field.from_args('gripper_field', np.float64, 'vector', omega, approx_order=APPROX_ORDER)

    #Generate field variables for use in computation: unknown will refer to the displacement
    u = FieldVariable('u', 'unknown', field)
    v = FieldVariable('v', 'test', field, primary_var_name='u')

    #TPU material constants for use in FEA analysis: might not be exactly correct but will verify later.
    #With a linear model and min-max normalized metrics, Young's modulus does not change the ranking.
    young = 12 # in Mpa
    poisson = 0.45 
    D = stiffness_from_youngpoisson(3, young, poisson)

    #TPU Material object
    material = Material('m', D=D)

    integral = Integral('i', order=2 * APPROX_ORDER)

    #Traction = magnitude over the real area of the force region's facets, pointing into the material
    area_term = Term.new('ev_volume(u)', integral, regions["force"], u=u)
    area_term.setup()
    force_area = area_term.evaluate(mode='eval')
    traction = bcs["force"]["magnitude"] / force_area * bcs["force"]["direction"]
    print(f"force region: {regions['force'].facets.size} facets, area {force_area:.3f} mm^2, "
          f"traction {np.round(traction, 4).tolist()} MPa")
    force = Material('force', values={'val': traction.reshape(3, 1)})

    t1 = Term.new('dw_lin_elastic(m.D, v, u)', integral, omega, m=material, v=v, u=u)
    t2 = Term.new('dw_surface_ltr(force.val, v)', integral, regions["force"], force=force, v=v)

    #internal forces = applied load (t1 + t2 = 0 would apply the traction in the opposite direction)
    eq = Equation('balance', t1 - t2)
    eqs = Equations([eq])

    #Clamp every fixed face
    fixes = [EssentialBC(f'fix_{k}', region, {'u.all' : 0.0}) for k, region in enumerate(regions["fixed"])]

    nls_status = IndexedStruct()
    nls = Newton({}, lin_solver=LinearSolver({}), status=nls_status)

    pb = Problem('compliant_gripper_metrics', equations=eqs)
    pb.set_bcs(ebcs=Conditions(fixes))

    pb.set_solver(nls)
    status = IndexedStruct()
    pb.solve(status=status, save_results=False)

    #Stress at the quadrature points (not element averages, which smear out bending stress
    #through a rod's thickness), with each point's share of the volume for percentile weighting
    stress = pb.evaluate('ev_cauchy_stress.i.Omega(m.D, u)', mode='qp', m=material,
                         integrals={'i': integral})
    von_mises = cauchy_to_von_mises(stress.reshape(-1, 6).T)
    geo, _ = field.get_mapping(omega, integral, 'cell')
    qp_volume = np.asarray(geo.det).reshape(-1)

    u_patch = np.asarray(pb.evaluate('ev_integrate.i.Gamma_force(u)', integrals={'i': integral})).ravel()
    load_point_disp = float(np.dot(u_patch, bcs["force"]["direction"]) / force_area)

    return von_mises, qp_volume, load_point_disp
