import matplotlib.pyplot as plt
import numpy as np
import math

def cauchy_to_von_mises(cauchyArray):
    """von Mises stress from the 6 Cauchy stress components (normal components first,
    then the 3 shear components, in any order). Works on scalars or arrays."""

    sigma_x, sigma_y, sigma_z, tau_xy, tau_yz, tau_xz = cauchyArray

    #shear counts 3x: sqrt(0.5*[(sx-sy)^2 + (sy-sz)^2 + (sz-sx)^2] + 3*(txy^2 + tyz^2 + txz^2))
    shear_constant = 3 * (tau_xy**2 + tau_yz**2 + tau_xz**2)
    sigma_constant = 0.5 * ((sigma_x-sigma_y)**2 + (sigma_y-sigma_z)**2 + (sigma_z-sigma_x)**2)

    return (shear_constant+ sigma_constant) ** 0.5

def minmax(x):
    x = np.array(x, dtype=float)

    min_val = np.min(x)
    max_val = np.max(x)

    if max_val == min_val:
        return np.zeros_like(x)

    return (x - min_val) / (max_val - min_val)

def weighted_percentile_band(values, weights, lo=90, hi=95):
    """Weighted mean of the values between the lo-th and hi-th weighted percentiles.

    With volume weights this is "the stress level the most-stressed 5-10% of the
    material sees": high enough to reflect where the part works hardest, while
    the top 5% (clamp edges, sharp corners, the edge of the force patch, where
    stress grows without limit as the mesh is refined) is left out.
    """
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    keep = np.isfinite(values) & (weights > 0)
    values, weights = values[keep], weights[keep]
    order = np.argsort(values)
    values, weights = values[order], weights[order]
    cum = np.cumsum(weights) / weights.sum() * 100
    band = (cum >= lo) & (cum <= hi)
    if not band.any():  # very coarse meshes: fall back to the point nearest the band
        band = np.zeros_like(band)
        band[min(np.searchsorted(cum, (lo + hi) / 2), len(cum) - 1)] = True
    return float(np.sum(values[band] * weights[band]) / np.sum(weights[band]))
