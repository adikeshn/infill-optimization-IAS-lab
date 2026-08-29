import cadquery as cq
import matplotlib.pyplot as plt
import math
import numpy as np

def create_triangle_outline(part, outer_thickness=1):
    bbox = part.val().BoundingBox()
    slope = (bbox.zmax - bbox.zmin) / (bbox.xmax - bbox.xmin)
    intercept = bbox.zmax
    normal_shift = outer_thickness * math.sqrt(1 + slope**2)
    new_intercept = intercept - normal_shift

    def outline_line(x, return_z=True):
        if return_z:
            return slope * x + new_intercept
        else:
            return (x - new_intercept) / slope

    inner = (
        cq.Workplane("XZ")
        .polyline([
            (-outer_thickness, outer_thickness),
            (-outer_thickness, outline_line(-outer_thickness)),
            (outline_line(outer_thickness, return_z=False), outer_thickness)
        ])
        .close()
        .extrude(-(bbox.ymax - bbox.ymin))
    )

    hollow_triangle = part.cut(inner)
    inner_vol = part.val().Volume()

    return hollow_triangle, inner, inner_vol

def _rot_xz(x, z, angle_deg):
  a = math.radians(angle_deg)
  ca = math.cos(a)
  sa = math.sin(a)
  return x * ca - z * sa, x * sa + z * ca

def _align_to_grid(val, spacing, base=0.0):
  return base + math.floor((val - base) / spacing) * spacing

def get_finray_infill(
  part,
  density=25,
  spacing=0,
  rod_diameter=0.45,
  outline_thickness=0.87,
  angle_deg=-60,
  debug=True
):

  outline, inner, inner_vol = create_triangle_outline(
    part,
    outer_thickness=outline_thickness
  )

  bbox = part.val().BoundingBox()

  cx = (bbox.xmin + bbox.xmax) / 2
  cy = (bbox.ymin + bbox.ymax) / 2
  cz = (bbox.zmin + bbox.zmax) / 2

  infill_thickness = bbox.ymax - bbox.ymin

  if spacing == 0:
    density_frac = density / 100.0
    spacing = rod_diameter / density_frac


  corners = [
    (bbox.xmin - cx, bbox.zmin - cz),
    (bbox.xmin - cx, bbox.zmax - cz),
    (bbox.xmax - cx, bbox.zmin - cz),
    (bbox.xmax - cx, bbox.zmax - cz),
  ]

  rot_corners = [_rot_xz(x, z, -angle_deg) for x, z in corners]

  xs = [p[0] for p in rot_corners]
  zs = [p[1] for p in rot_corners]

  x_min = min(xs)
  x_max = max(xs)
  z_min = min(zs)
  z_max = max(zs)

  x_start = _align_to_grid(x_min, spacing, 0.0)
  x_end = x_max + spacing


  rod_len = (z_max - z_min) + 2 * spacing
  rod_mid_z = (z_min + z_max) / 2

  grid = cq.Workplane("XZ")

  x = x_start
  rod_index = 0

  while x <= x_end:

    rod = (
      cq.Workplane("XZ")
      .center(x, rod_mid_z)
      .rect(rod_diameter, rod_len)
      .extrude(-infill_thickness)
    )
    grid = grid.union(rod)

    x += spacing
    rod_index += 1

  rotated_grid = (
    grid
    .translate((0, cy, 0))
    .rotate((0, cy, 0), (0, cy + 1, 0), angle_deg)
    .translate((cx, -cy, cz))
  )

  infill_inside = rotated_grid.intersect(part)

  result = outline.union(infill_inside)

  infill_volume = infill_inside.val().Volume()

  return result, infill_volume / inner_vol * 100

def get_grid_infill(
  part,
  density=50,
  rod_diameter=0.45,
  spacing=0,
  outline_thickness=0.87,
  angle_deg=45,
):
  outline, inner, inner_vol = create_triangle_outline(
    part,
    outer_thickness=outline_thickness
  )

  if density <= 0 or density > 100:
    raise ValueError("density must be in (0, 100].")

  bbox = part.val().BoundingBox()
  cx = (bbox.xmin + bbox.xmax) / 2
  cy = (bbox.ymin + bbox.ymax) / 2
  cz = (bbox.zmin + bbox.zmax) / 2

  infill_thickness = bbox.ymax - bbox.ymin

  if spacing == 0:
    density_frac = density / 100.0
    r = 1 - math.sqrt(1 - density_frac)
    spacing = rod_diameter / r

  corners = [
    (bbox.xmin - cx, bbox.zmin - cz),
    (bbox.xmin - cx, bbox.zmax - cz),
    (bbox.xmax - cx, bbox.zmin - cz),
    (bbox.xmax - cx, bbox.zmax - cz),
  ]

  a = math.radians(-angle_deg)
  ca = math.cos(a)
  sa = math.sin(a)

  rot_corners = []
  for x, z in corners:
    xr = x * ca - z * sa
    zr = x * sa + z * ca
    rot_corners.append((xr, zr))

  xs = [p[0] for p in rot_corners]
  zs = [p[1] for p in rot_corners]

  x_min = min(xs)
  x_max = max(xs)
  z_min = min(zs)
  z_max = max(zs)

  x_start = _align_to_grid(x_min, spacing, 0.0)
  z_start = _align_to_grid(z_min, spacing, 0.0)

  x_end = x_max + spacing
  z_end = z_max + spacing

  rod_len_x = (x_max - x_min) + 2 * spacing
  rod_len_z = (z_max - z_min) + 2 * spacing

  x_mid = (x_min + x_max) / 2
  z_mid = (z_min + z_max) / 2

  grid = cq.Workplane("XZ")

  x = x_start
  while x <= x_end:
    rod = (
      cq.Workplane("XZ")
      .center(x, z_mid)
      .rect(rod_diameter, rod_len_z)
      .extrude(-infill_thickness)
    )
    grid = grid.union(rod)
    x += spacing

  z = z_start
  while z <= z_end:
    rod = (
      cq.Workplane("XZ")
      .center(x_mid, z)
      .rect(rod_len_x, rod_diameter)
      .extrude(-infill_thickness)
    )
    grid = grid.union(rod)
    z += spacing

  grid = (
    grid
    .translate((0, cy, 0))
    .rotate((0, cy, 0), (0, cy + 1, 0), angle_deg)
    .translate((cx, -cy, cz-0.2))
  )
  infill_inside = grid.intersect(part)

  result = outline.union(infill_inside)
  infill_volume = infill_inside.val().Volume()
  density_percent = infill_volume / inner_vol * 100

  return result, density_percent

def _build_parallel_family(
  x_start,
  x_end,
  z_mid,
  rod_len,
  rod_diameter,
  infill_thickness,
  spacing
):
  family = cq.Workplane("XZ")

  x = x_start
  while x <= x_end:
    rod = (
      cq.Workplane("XZ")
      .center(x, z_mid)
      .rect(rod_diameter, rod_len)
      .extrude(-infill_thickness)
    )
    family = family.union(rod)
    x += spacing

  return family

def get_triangle_infill(
  part,
  spacing=0,
  density=50,
  rod_diameter=0.45,
  outline_thickness=0.87,
  angle_deg=45
):
  outline, inner, inner_vol = create_triangle_outline(
    part,
    outer_thickness=outline_thickness
  )

  if density <= 0 or density > 100:
    raise ValueError("density must be in (0, 100].")

  final_angle_deg = angle_deg

  bbox = part.val().BoundingBox()
  cx = (bbox.xmin + bbox.xmax) / 2
  cy = (bbox.ymin + bbox.ymax) / 2
  cz = (bbox.zmin + bbox.zmax) / 2

  infill_thickness = bbox.ymax - bbox.ymin

  if spacing == 0:
    density_frac = density / 100.0
    r = (2.0 / 3.0) * (1.0 - math.sqrt(1.0 - density_frac))
    spacing = rod_diameter / r

  corners = [
    (bbox.xmin - cx, bbox.zmin - cz),
    (bbox.xmin - cx, bbox.zmax - cz),
    (bbox.xmax - cx, bbox.zmin - cz),
    (bbox.xmax - cx, bbox.zmax - cz),
  ]

  rot_corners = [_rot_xz(x, z, -final_angle_deg) for x, z in corners]
  xs = [p[0] for p in rot_corners]
  zs = [p[1] for p in rot_corners]

  x_min = min(xs)
  x_max = max(xs)
  z_min = min(zs)
  z_max = max(zs)

  x_mid = (x_min + x_max) / 2
  z_mid = (z_min + z_max) / 2

  span_x = x_max - x_min
  span_z = z_max - z_min
  bed_span = math.sqrt(span_x * span_x + span_z * span_z) + 4 * spacing

  x_start = _align_to_grid(x_mid - bed_span / 2, spacing, 0.0)
  x_end = x_mid + bed_span / 2 + spacing

  family_0 = _build_parallel_family(
    x_start,
    x_end,
    z_mid,
    bed_span,
    rod_diameter,
    infill_thickness,
    spacing
  )

  family_pos60 = (
    _build_parallel_family(
      x_start,
      x_end,
      z_mid,
      bed_span,
      rod_diameter,
      infill_thickness,
      spacing
    )
    .rotate((x_mid, 0, z_mid), (x_mid, 1, z_mid), 60)
  )

  family_neg60 = (
    _build_parallel_family(
      x_start,
      x_end,
      z_mid,
      bed_span,
      rod_diameter,
      infill_thickness,
      spacing
    )
    .rotate((x_mid, 0, z_mid), (x_mid, 1, z_mid), -60)
  )

  grid = family_0.union(family_pos60).union(family_neg60)

  grid = (
    grid
    .translate((0, cy, 0))
    .rotate((0, cy, 0), (0, cy + 1, 0), final_angle_deg)
    .translate((cx, -cy, cz-0.87))
  )

  infill_inside = grid.intersect(part)

  result = outline.union(infill_inside)
  infill_volume = infill_inside.val().Volume()

  return result, infill_volume / inner_vol * 100

def _hex_ring_from_inner(inner_side_length, wall_offset, depth):
  pts = []
  for i in range(6):
    angle = math.radians(60 * i + 30)
    pts.append((
      math.cos(angle) * inner_side_length,
      math.sin(angle) * inner_side_length
    ))

  outer = (
    cq.Workplane("XZ")
    .polyline(pts)
    .close()
    .offset2D(wall_offset, kind="intersection")
    .extrude(-depth)
  )

  inner = (
    cq.Workplane("XZ")
    .polyline(pts)
    .close()
    .extrude(-depth)
  )

  return outer.cut(inner)

def get_honeycomb_infill(
  part,
  side_length=0,
  density=20,
  rod_diameter=0.87,
  outline_thickness=1.154,
  layer_idx=0,
  emulate_prusa_layer_angle=False,
  angle_deg=-45
):
  outline, inner, inner_vol = create_triangle_outline(
    part,
    outer_thickness=outline_thickness
  )

  if density <= 0 or density >= 100:
    raise ValueError("density must be in (0, 100) for honeycomb.")

  bbox = part.val().BoundingBox()
  cx = (bbox.xmin + bbox.xmax) / 2
  cy = (bbox.ymin + bbox.ymax) / 2
  cz = (bbox.zmin + bbox.zmax) / 2

  infill_thickness = bbox.ymax - bbox.ymin

  density_wall_offset = rod_diameter / 2.0
    
  if side_length == 0:
    density_frac = density / 100.0
    q = math.sqrt(1.0 - density_frac)
    k = 2.0 * density_wall_offset / math.sqrt(3.0)
    side_length = k * q / (1.0 - q)

  outer_side_length = side_length + 2.0 * density_wall_offset / math.sqrt(3.0)

  dx = math.sqrt(3.0) * outer_side_length
  dz = 1.5 * outer_side_length

  final_angle_deg = angle_deg
  if emulate_prusa_layer_angle:
    final_angle_deg += 60 * (layer_idx % 3)

  corners = [
    (bbox.xmin - cx, bbox.zmin - cz),
    (bbox.xmin - cx, bbox.zmax - cz),
    (bbox.xmax - cx, bbox.zmin - cz),
    (bbox.xmax - cx, bbox.zmax - cz),
  ]

  rot_corners = [_rot_xz(x, z, -final_angle_deg) for x, z in corners]
  xs = [p[0] for p in rot_corners]
  zs = [p[1] for p in rot_corners]

  x_min = min(xs)
  x_max = max(xs)
  z_min = min(zs)
  z_max = max(zs)

  z_start = _align_to_grid(z_min - 0.5 * dz, dz, 0.0)
  z_end = z_max + 0.5 * dz

  grid = cq.Workplane("XZ")

  row_idx = 0
  z = z_start
  while z <= z_end:
    x_shift = 0.0 if (row_idx % 2 == 0) else (dx / 2.0)
    x_start = _align_to_grid(x_min - 2.0 * dx - x_shift, dx, 0.0) + x_shift
    x_end = x_max + 2.0 * dx

    x = x_start
    while x <= x_end:
      cell = _hex_ring_from_inner(
        side_length,
        density_wall_offset,
        infill_thickness
      ).translate((x, 0, z))

      grid = grid.union(cell)
      x += dx

    z += dz
    row_idx += 1

  grid = (
    grid
    .translate((0, cy, 0))
    .rotate((0, cy, 0), (0, cy + 1, 0), final_angle_deg)
    .translate((cx, -cy, cz))
  )

  infill_inside = grid.intersect(part)

  result = outline.union(infill_inside)
  infill_volume = infill_inside.val().Volume()

  return result, infill_volume / inner_vol * 100