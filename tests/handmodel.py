"""One synthetic hand, in metres, projected through a pinhole camera.

Shared by the pinch tests and the fist tests because it has to be the same hand:
both gestures are decided from the metric landmarks and both are checked against
what the projection does to them, and two fixtures drifting apart would let one
suite prove something the other quietly contradicts.

Built in metres first and projected second, because that is the order the real
pipeline works in and it is the only way the orientation tests can mean
anything: a fixture that made up the 2D and the 3D landmarks independently could
be made to agree with whatever it was asked to prove.

The proportions are not invented. Palm length, fingertip gaps and finger lengths
are all taken off real MediaPipe world landmarks -- photographs of open hands,
pinches and clenched fists -- so an open hand here reads 0.78 on the fist ratio
and a clenched one 0.32, which is where real hands read (0.74-0.98 and
0.30-0.36).
"""

import math

from kinesis.tracking.gestures import Detection, pinch_point

PALM_M = 0.095        # wrist -> middle MCP, measured off a real detected hand
PINCHED_M = 0.014     # fingertips together: 0.147 of the palm
OPEN_M = 0.050        # fingertips apart: 0.53 of the palm
FIST_GAP_M = 0.036    # thumb tip to index tip in a clenched fist: 0.38 of the palm

# Index, middle, ring, pinky: length from the knuckle, and offset along the
# knuckle line, both as fractions of the palm.
FINGER_LEN = (0.75, 0.90, 0.80, 0.65)
FINGER_OFFSET = (-0.22, 0.0, 0.20, 0.38)

# A clenched finger is an arc of roughly this much turn, which puts the tip
# 0.41 of its own length from its knuckle -- the fist ratio real fists read.
FULL_CURL_DEG = 240.0

FRAME_W, FRAME_H = 640, 480                            # 4:3, as the camera runs
FOCAL = (FRAME_W / 2) / math.tan(math.radians(30))     # ~60 deg horizontal FOV
DEPTH = 0.50          # metres from the lens


def _finger_tip(mcp, length, curl, up, normal):
    """Where a finger of this length points after curling `curl` of the way in.

    An arc, not a hinge: the finger leaves the knuckle along the palm axis and
    bends toward the palm, so the tip sweeps in and back down the way a real one
    does. A straight finger is the arc of zero turn, handled separately only
    because the radius goes to infinity there.
    """
    theta = math.radians(FULL_CURL_DEG) * curl
    if theta < 1e-6:
        return tuple(m + length * u for m, u in zip(mcp, up))
    radius = length / theta
    along = radius * math.sin(theta)
    into = radius * (1.0 - math.cos(theta))
    return tuple(m + along * u + into * n for m, u, n in zip(mcp, up, normal))


def world_hand(gap: float, curl: float = 0.0, tilt: float = 0.0, roll: float = 0.0):
    """21 metric landmarks: `gap` metres between the fingertips, `curl` closed.

    `curl` runs 0 (fingers straight) to 1 (clenched). `tilt` turns the hand
    toward the lens, so the palm axis foreshortens under projection while the
    fingertip gap does not -- the geometry of #32, and the worst case for a
    fist, whose every fingertip then sits over its own knuckle. `roll` turns the
    pinch axis within the palm plane, from across the palm at 0 to along it at
    90, which is what the per-axis 2D normalization treats unevenly.

    Only the points the maths reads are placed; the rest sit mid-palm.
    """
    a, r = math.radians(tilt), math.radians(roll)
    up = (0.0, -math.cos(a), -math.sin(a))             # wrist -> fingers
    side = (1.0, 0.0, 0.0)                             # across the knuckles
    normal = (side[1] * up[2] - side[2] * up[1],       # out of the palm, toward the lens
              side[2] * up[0] - side[0] * up[2],
              side[0] * up[1] - side[1] * up[0])
    axis = (math.cos(r),                                # thumb tip -> index tip
            math.sin(r) * up[1],
            math.sin(r) * up[2])
    mmcp = tuple(PALM_M * c for c in up)
    pts = [tuple(c / 2 for c in mmcp)] * 21
    pts[0] = (0.0, 0.0, 0.0)
    for tip, knuckle, length, offset in zip((8, 12, 16, 20), (5, 9, 13, 17),
                                            FINGER_LEN, FINGER_OFFSET):
        mcp = tuple(m + offset * PALM_M * s for m, s in zip(mmcp, side))
        pts[knuckle] = mcp
        pts[tip] = _finger_tip(mcp, length * PALM_M, curl, up, normal)
    pts[4] = tuple(c - a_ * gap for c, a_ in zip(pts[8], axis))
    return pts


def project(world, at=None, depth: float = DEPTH):
    """Pinhole-project metres to normalized frame coords, per axis over 4:3.

    Dividing x by 640 and y by 480 is what MediaPipe hands back, and is where
    the 1.33x anisotropy in the old projected ratio came from.
    """
    pts = [((FOCAL * x / (z + depth) + FRAME_W / 2) / FRAME_W,
            (FOCAL * y / (z + depth) + FRAME_H / 2) / FRAME_H) for x, y, z in world]
    if at is None:
        return pts
    mid = pinch_point(pts)
    return [(x + at[0] - mid[0], y + at[1] - mid[1]) for x, y in pts]


def det(gap: float = OPEN_M, curl: float = 0.0, tilt: float = 0.0, roll: float = 0.0,
        at=(0.5, 0.4), depth: float = DEPTH, label: str = "Right") -> Detection:
    world = world_hand(gap, curl, tilt, roll)
    return Detection(label, project(world, at, depth), world)


def feed(engine, poses, dt=1 / 30, start=0.0, curl=0.0, **kw):
    """Run a sequence of gaps (or (gap, curl) pairs) through, returning each Hand."""
    out, t = [], start
    for pose in poses:
        gap, this_curl = pose if isinstance(pose, tuple) else (pose, curl)
        hands = engine.update([det(gap, this_curl, **kw)], t)
        out.append(hands[0] if hands else None)
        t += dt
    return out
