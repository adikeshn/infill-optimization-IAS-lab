import matplotlib.pyplot as plt
import numpy as np
import math

def minmax(x):
        return (x - np.min(x)) / (np.max(x) - np.min(x))

def cauchy_to_von_mises(cauchyArray):

    sigma_x, sigma_y, sigma_z, tau_xy, tau_yz, tau_xz = cauchyArray

    shear_constant = tau_xy**2 + tau_yz**2 + tau_xz**2
    sigma_constant = 0.5 * ((sigma_x-sigma_y)**2 + (sigma_y-sigma_z)**2 + (sigma_z-sigma_x)**2)

    return (shear_constant+ sigma_constant) ** 0.5

def minmax(x):
    x = np.array(x, dtype=float)

    min_val = np.min(x)
    max_val = np.max(x)

    if max_val == min_val:
        return np.zeros_like(x)

    return (x - min_val) / (max_val - min_val)

def computePseudoCGS(disp_flat, stress):

    disp_magnitude = np.linalg.norm(disp_flat, axis=1)  

    stress_flat = stress.squeeze()  
    
    von_mises_stress = []
    for cauchy_stress in stress_flat:
        von_mises_stress.append(cauchy_to_von_mises(cauchy_stress))
    

    compliancy = max(disp_magnitude)
    force_output = 1 - max(von_mises_stress)
    pseudoCGS = (compliancy + force_output) / 2

    return von_mises_stress, disp_flat
