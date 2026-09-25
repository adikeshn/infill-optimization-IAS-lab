import cadquery as cq
import math


def _is_y_normal(face):
  return face.geomType() == "PLANE" and abs(abs(face.normalAt().y) - 1.0) < 1e-6


def cross_section_face(part):
  """The part's planar face normal to Y, i.e. the cross-section of the Y prism."""
  solid = part.val()
  faces = [f for f in solid.Faces() if _is_y_normal(f)]
  if not faces:
    raise ValueError(
      "The part has no planar face normal to Y. Only grippers that are prisms "
      "along Y (a constant XZ cross-section extruded in Y) are supported."
    )
  face = max(faces, key=lambda f: f.Area())

  bbox = solid.BoundingBox()
  if abs(face.Area() * (bbox.ymax - bbox.ymin) - solid.Volume()) > 0.01 * solid.Volume():
    raise ValueError(
      "The part is not a prism along Y: its volume does not match its Y-face "
      "area times its Y depth. Only a constant XZ cross-section extruded in Y "
      "is supported."
    )
  return face


def _offset(wire, d):
  """Faces bounded by `wire` grown by d (d > 0) or shrunk by -d (d < 0).

  offset2D's sign convention follows the wire's orientation, which differs
  between outer and hole wires, so pick the sign by comparing areas.
  """
  base = cq.Face.makeFromWires(wire).Area()
  for s in (d, -d):
    try:
      faces = [cq.Face.makeFromWires(w) for w in wire.offset2D(s, kind="intersection")]
    except Exception:
      faces = []  # shrinking can collapse the wire entirely
    area = sum(f.Area() for f in faces)
    if (area > base) == (d > 0):
      return faces
  return []


def make_shell(part, wall):
  """Split `part` into a shell of thickness `wall` and the cavity it encloses.

  The cavity is the Y cross-section offset inward by `wall` (holes offset
  outward), extruded through the part's full Y depth, so the two Y faces get
  no wall and the infill is exposed there.
  """
  face = cross_section_face(part)
  bbox = part.val().BoundingBox()
  shift = cq.Vector(0, bbox.ymin - face.Center().y, 0)
  depth = cq.Vector(0, bbox.ymax - bbox.ymin, 0)

  def prism(faces):
    solids = [cq.Solid.extrudeLinear(f.translate(shift), depth) for f in faces]
    return solids[0].fuse(*solids[1:]) if len(solids) > 1 else solids[0]

  outer = _offset(face.outerWire(), -wall)
  if not outer:
    raise ValueError(
      f"An outline thickness of {wall} mm leaves no room for infill in this "
      "cross-section. Use a thinner outline."
    )
  cavity = prism(outer)
  holes = [prism(_offset(w, wall)) for w in face.innerWires()]
  if holes:
    cavity = cavity.cut(*holes)

  return part.cut(cavity), cavity


def _lattice_bounds(part, angle_deg):
  """Part bbox (XZ, about its center) in the lattice frame rotated by angle_deg."""
  bbox = part.val().BoundingBox()
  cx = (bbox.xmin + bbox.xmax) / 2
  cz = (bbox.zmin + bbox.zmax) / 2
  corners = [(x - cx, z - cz) for x in (bbox.xmin, bbox.xmax) for z in (bbox.zmin, bbox.zmax)]
  rot_corners = [_rot_xz(x, z, -angle_deg) for x, z in corners]
  xs = [p[0] for p in rot_corners]
  zs = [p[1] for p in rot_corners]
  return min(xs), max(xs), min(zs), max(zs)


def _fill(part, lattice, angle_deg, outline_thickness):
  """Place a lattice built about the XZ origin (spanning y=0..depth) in the part's
  cavity and union it with the shell. Returns (solid, infill % of cavity volume)."""
  shell, cavity = make_shell(part, outline_thickness)
  bbox = part.val().BoundingBox()

  placed = (
    lattice
    .rotate((0, 0, 0), (0, 1, 0), angle_deg)
    .translate(((bbox.xmin + bbox.xmax) / 2, bbox.ymin, (bbox.zmin + bbox.zmax) / 2))
  )
  infill_inside = placed.intersect(cavity)
  infill_volume = infill_inside.val().Volume()
  if infill_volume <= 0:
    raise ValueError("No infill fits inside the cavity. Use a thinner outline or a denser infill.")

  return shell.union(infill_inside), infill_volume / cavity.Volume() * 100


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
  bbox = part.val().BoundingBox()
  depth = bbox.ymax - bbox.ymin

  if spacing == 0:
    density_frac = density / 100.0
    spacing = rod_diameter / density_frac

  x_min, x_max, z_min, z_max = _lattice_bounds(part, angle_deg)

  x_start = _align_to_grid(x_min, spacing, 0.0)
  x_end = x_max + spacing

  rod_len = (z_max - z_min) + 2 * spacing
  rod_mid_z = (z_min + z_max) / 2

  grid = _build_parallel_family(
    x_start,
    x_end,
    rod_mid_z,
    rod_len,
    rod_diameter,
    depth,
    spacing
  )

  return _fill(part, grid, angle_deg, outline_thickness)

def get_grid_infill(
  part,
  density=50,
  rod_diameter=0.45,
  spacing=0,
  outline_thickness=0.87,
  angle_deg=45,
):
  if density <= 0 or density > 100:
    raise ValueError("density must be in (0, 100].")

  bbox = part.val().BoundingBox()
  depth = bbox.ymax - bbox.ymin

  if spacing == 0:
    density_frac = density / 100.0
    r = 1 - math.sqrt(1 - density_frac)
    spacing = rod_diameter / r

  x_min, x_max, z_min, z_max = _lattice_bounds(part, angle_deg)

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
      .extrude(-depth)
    )
    grid = grid.union(rod)
    x += spacing

  z = z_start
  while z <= z_end:
    rod = (
      cq.Workplane("XZ")
      .center(x_mid, z)
      .rect(rod_len_x, rod_diameter)
      .extrude(-depth)
    )
    grid = grid.union(rod)
    z += spacing

  return _fill(part, grid, angle_deg, outline_thickness)

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
  if density <= 0 or density > 100:
    raise ValueError("density must be in (0, 100].")

  bbox = part.val().BoundingBox()
  depth = bbox.ymax - bbox.ymin

  if spacing == 0:
    density_frac = density / 100.0
    r = (2.0 / 3.0) * (1.0 - math.sqrt(1.0 - density_frac))
    spacing = rod_diameter / r

  x_min, x_max, z_min, z_max = _lattice_bounds(part, angle_deg)

  x_mid = (x_min + x_max) / 2
  z_mid = (z_min + z_max) / 2

  span_x = x_max - x_min
  span_z = z_max - z_min
  bed_span = math.sqrt(span_x * span_x + span_z * span_z) + 4 * spacing

  x_start = _align_to_grid(x_mid - bed_span / 2, spacing, 0.0)
  x_end = x_mid + bed_span / 2 + spacing

  def family(rotation):
    return _build_parallel_family(
      x_start,
      x_end,
      z_mid,
      bed_span,
      rod_diameter,
      depth,
      spacing
    ).rotate((x_mid, 0, z_mid), (x_mid, 1, z_mid), rotation)

  grid = family(0).union(family(60)).union(family(-60))

  return _fill(part, grid, angle_deg, outline_thickness)

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
  if density <= 0 or density >= 100:
    raise ValueError("density must be in (0, 100) for honeycomb.")

  bbox = part.val().BoundingBox()
  depth = bbox.ymax - bbox.ymin

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

  x_min, x_max, z_min, z_max = _lattice_bounds(part, final_angle_deg)

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
        depth
      ).translate((x, 0, z))

      grid = grid.union(cell)
      x += dx

    z += dz
    row_idx += 1

  return _fill(part, grid, final_angle_deg, outline_thickness)
