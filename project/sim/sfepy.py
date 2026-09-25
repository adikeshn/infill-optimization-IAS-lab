
import numpy as np
from sfepy.discrete.fem import Mesh, Field
from sfepy.discrete import (Material, FieldVariable, Integral, Equation, Equations, Problem)
from sfepy.terms import Term
from sfepy.discrete import Problem
from sfepy.mechanics.matcoefs import stiffness_from_youngpoisson
from sfepy.discrete.fem import FEDomain
from sfepy.discrete.conditions import EssentialBC, Conditions
from sfepy.solvers.ls import ScipyDirect
from sfepy.solvers.nls import Newton 
from sfepy.base.base import IndexedStruct
from sim.faces import points_near_triangles
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


def calc_gripper_results(omega, regions, bcs):

    field = Field.from_args('gripper_field', np.float64, 'vector', omega, approx_order=1)


    #Generate field variables for use in computation: unknown will refer to the displacement
    u = FieldVariable('u', 'unknown', field)
    v = FieldVariable('v', 'test', field, primary_var_name='u')

    #TPU material constants for use in FEA analysis: might not be exactly correct but will verify later
    young = 12 # in Mpa
    poisson = 0.45 
    D = stiffness_from_youngpoisson(3, young, poisson)

    #TPU Material object
    material = Material('m', D=D)

    integral = Integral('i', order=2)

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

    eq = Equation('balance', t1 + t2)
    eqs = Equations([eq])

    #Clamp every fixed face
    fixes = [EssentialBC(f'fix_{k}', region, {'u.all' : 0.0}) for k, region in enumerate(regions["fixed"])]

    ls = ScipyDirect({})
    nls_status = IndexedStruct()
    nls = Newton({}, lin_solver=ls, status=nls_status)

    pb = Problem('compliant_gripper_metrics', equations=eqs)
    pb.set_bcs(ebcs=Conditions(fixes))

    pb.set_solver(nls)
    status = IndexedStruct()
    variables = pb.solve(status=status)

    stress = pb.evaluate(
    'ev_cauchy_stress.i.Omega(m.D, u)',
    'Omega',
    mode='el_avg',
    u=variables,
    m=material,                 
    integrals={'i': integral},  
    )

    u_var = variables['u']
    disp_array = np.array(u_var.data)  
    disp = disp_array.reshape((-1, u_var.n_components))
    
    return stress, disp



