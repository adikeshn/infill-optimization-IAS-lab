
import numpy as np
from sfepy.discrete.fem import Mesh, Field
from sfepy.discrete import (Material, FieldVariable, Integral, Equation, Equations, Problem, Function)
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


def resolve_material(sim_space):
    """Material settings from the UI, defaulting to the TPU constants this app
    has used since the start (see the note on `calc_gripper_results` about
    these being provisional)."""
    cfg = sim_space.get("material") or {}
    try:
        young = float(cfg.get("young", 12))
        poisson = float(cfg.get("poisson", 0.45))
    except (TypeError, ValueError):
        raise ValueError("Young's modulus and Poisson's ratio must be numbers.")
    if young <= 0:
        raise ValueError("Young's modulus must be positive.")
    if not (-1 < poisson < 0.5):
        raise ValueError("Poisson's ratio must be between -1 and 0.5 (exclusive).")
    return {"young": young, "poisson": poisson, "hyperelastic": bool(cfg.get("hyperelastic", False))}


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

#Load stepping for the hyperelastic solve, as fractions of the full force: the first increment,
#and the smallest increment to cut back to before giving up.
HYPER_FIRST_STEP = 0.1
HYPER_MIN_STEP = 0.005


def calc_gripper_results(omega, regions, bcs, material):
    """Linear-elastic or (optionally) nearly-incompressible Neo-Hookean solve, selected
    by `material["hyperelastic"]`. Returns (von Mises stress at every quadrature point
    [MPa], the volume each quadrature point represents [mm^3], and the load-point
    displacement: the area-averaged displacement of the force patch along the force
    direction [mm]).

    `material["young"]`/`["poisson"]` are provisional TPU constants (see the UI) that
    the user can override; with a linear model and min-max normalized metrics, Young's
    modulus does not change the ranking. The hyperelastic path calibrates its shear/bulk
    moduli from the same (young, poisson) so the two models agree in the small-strain
    limit, but TPU really is hyperelastic at the large deflections this app targets -
    see the README's "FEA model and metrics" for what that path does differently.
    """

    field = Field.from_args('gripper_field', np.float64, 'vector', omega, approx_order=APPROX_ORDER)

    #Generate field variables for use in computation: unknown will refer to the displacement
    u = FieldVariable('u', 'unknown', field)
    v = FieldVariable('v', 'test', field, primary_var_name='u')

    young, poisson = material["young"], material["poisson"]

    integral = Integral('i', order=2 * APPROX_ORDER)

    #Traction = magnitude over the real area of the force region's facets, pointing into the material.
    #This is a dead load (fixed direction and magnitude, integrated over the undeformed surface), so
    #the same term is correct whether the internal energy below is linear or hyperelastic.
    area_term = Term.new('ev_volume(u)', integral, regions["force"], u=u)
    area_term.setup()
    force_area = area_term.evaluate(mode='eval')
    traction = bcs["force"]["magnitude"] / force_area * bcs["force"]["direction"]
    print(f"force region: {regions['force'].facets.size} facets, area {force_area:.3f} mm^2, "
          f"traction {np.round(traction, 4).tolist()} MPa")

    #Clamp every fixed face
    fixes = [EssentialBC(f'fix_{k}', region, {'u.all' : 0.0}) for k, region in enumerate(regions["fixed"])]

    if material["hyperelastic"]:
        #Nearly-incompressible Neo-Hookean, Total Lagrangian formulation (dw_tl_he_neohook +
        #dw_tl_bulk_penalty, the standard SfePy pairing for this - see sfepy's own
        #examples/large_deformation/hyperelastic.py). mu/K are the usual small-strain-equivalent
        #moduli for (young, poisson); the bulk penalty approach can ill-condition as poisson -> 0.5.
        mat = Material('m', mu=young / (2 * (1 + poisson)), K=young / (3 * (1 - 2 * poisson)))
        load = {"scale": 0.0}

        def force_fun(ts, coors, mode=None, **kwargs):
            if mode == 'qp':
                return {'val': np.tile(load["scale"] * traction.reshape(1, 3, 1), (coors.shape[0], 1, 1))}

        force = Material('force', function=Function('force_fun', force_fun))
        t_force = Term.new('dw_surface_ltr(force.val, v)', integral, regions["force"], force=force, v=v)
        t_nh = Term.new('dw_tl_he_neohook(m.mu, v, u)', integral, omega, m=mat, v=v, u=u)
        t_bulk = Term.new('dw_tl_bulk_penalty(m.K, v, u)', integral, omega, m=mat, v=v, u=u)
        eqs = Equations([Equation('balance', t_nh + t_bulk - t_force)])
        pb = Problem('compliant_gripper_metrics', equations=eqs)
        pb.set_bcs(ebcs=Conditions(fixes))

        #Two things are needed for Newton to converge at full load. Load stepping: the force is
        #ramped up, each increment starting from the last converged shape, and an increment that
        #fails is halved. And full Newton steps (ls_on): under large rotations a correct Newton step
        #often raises the residual norm on the way to converging, and the default line search,
        #which only accepts steps that lower it, shrinks every step to ~1% and stalls. Steps that
        #invert elements are still cut back (ls_red_warp), independently of ls_on.
        nls_status = IndexedStruct()
        nls = Newton({'i_max': 15, 'eps_a': 1e-8, 'eps_r': 1e-6, 'ls_on': 1e3},
                     lin_solver=LinearSolver({}), status=nls_status)
        pb.set_solver(nls)

        frac, step, state, last_error = 0.0, HYPER_FIRST_STEP, None, None
        while frac < 1.0:
            trial = min(frac + step, 1.0)
            load["scale"] = trial
            try:
                #Copies both ways: get_state() is a view of the live solution, which a failed
                #increment would otherwise overwrite, leaving the retry to start from it.
                variables = pb.solve(state0=None if state is None else state.copy(),
                                     status=IndexedStruct(), save_results=False)
                converged = nls_status.condition == 0
            except ValueError as e:  # an increment that inverts elements beyond the line search's reach
                converged, last_error = False, e
            if converged:
                frac, state = trial, variables.get_state().copy()
                step *= 1.5
            else:
                step /= 2
                if step < HYPER_MIN_STEP:
                    magnitude = bcs["force"]["magnitude"]
                    raise RuntimeError(
                        f"The hyperelastic solve did not converge past {frac * magnitude:.3g} N of the "
                        f"{magnitude:g} N load (increments cut down to {step * magnitude:.2g} N"
                        + (f"; last error: {last_error}" if last_error else "") + "). The design may "
                        "buckle or snap through near that load, which this load-controlled solve "
                        "can't follow."
                    )

        #Effective 2nd Piola-Kirchhoff stress at the quadrature points (both terms contribute),
        #pushed forward to true (Cauchy) stress via sigma = F.S.F^T / J so the von Mises value
        #means the same physical thing as in the linear path below.
        s_nh = pb.evaluate('dw_tl_he_neohook.i.Omega(m.mu, v, u)', mode='qp', term_mode='stress',
                           m=mat, integrals={'i': integral})
        s_bulk = pb.evaluate('dw_tl_bulk_penalty.i.Omega(m.K, v, u)', mode='qp', term_mode='stress',
                             m=mat, integrals={'i': integral})
        sxx, syy, szz, sxy, syz, sxz = (s_nh + s_bulk).reshape(-1, 6).T
        S = np.zeros((len(sxx), 3, 3))
        S[:, 0, 0], S[:, 1, 1], S[:, 2, 2] = sxx, syy, szz
        S[:, 0, 1] = S[:, 1, 0] = sxy
        S[:, 1, 2] = S[:, 2, 1] = syz
        S[:, 0, 2] = S[:, 2, 0] = sxz

        F = pb.evaluate('ev_def_grad.i.Omega(u)', mode='qp', term_mode='def_grad',
                        integrals={'i': integral}).reshape(-1, 3, 3)
        J = pb.evaluate('ev_def_grad.i.Omega(u)', mode='qp', term_mode='jacobian',
                        integrals={'i': integral}).reshape(-1)

        cauchy = np.einsum('nij,njk,nlk->nil', F, S, F) / J[:, None, None]
        von_mises = cauchy_to_von_mises(np.array([
            cauchy[:, 0, 0], cauchy[:, 1, 1], cauchy[:, 2, 2],
            cauchy[:, 0, 1], cauchy[:, 1, 2], cauchy[:, 0, 2],
        ]))

    else:
        D = stiffness_from_youngpoisson(3, young, poisson)
        mat = Material('m', D=D)
        force = Material('force', values={'val': traction.reshape(3, 1)})
        t_force = Term.new('dw_surface_ltr(force.val, v)', integral, regions["force"], force=force, v=v)
        t1 = Term.new('dw_lin_elastic(m.D, v, u)', integral, omega, m=mat, v=v, u=u)
        eqs = Equations([Equation('balance', t1 - t_force)])
        pb = Problem('compliant_gripper_metrics', equations=eqs)
        pb.set_bcs(ebcs=Conditions(fixes))

        nls = Newton({}, lin_solver=LinearSolver({}), status=IndexedStruct())
        pb.set_solver(nls)
        pb.solve(status=IndexedStruct(), save_results=False)

        #Stress at the quadrature points (not element averages, which smear out bending stress
        #through a rod's thickness), with each point's share of the volume for percentile weighting
        stress = pb.evaluate('ev_cauchy_stress.i.Omega(m.D, u)', mode='qp', m=mat,
                             integrals={'i': integral})
        von_mises = cauchy_to_von_mises(stress.reshape(-1, 6).T)

    geo, _ = field.get_mapping(omega, integral, 'cell')
    qp_volume = np.asarray(geo.det).reshape(-1)

    u_patch = np.asarray(pb.evaluate('ev_integrate.i.Gamma_force(u)', integrals={'i': integral})).ravel()
    load_point_disp = float(np.dot(u_patch, bcs["force"]["direction"]) / force_area)

    return von_mises, qp_volume, load_point_disp
