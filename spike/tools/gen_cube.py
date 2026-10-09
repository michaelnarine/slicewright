# SPDX-License-Identifier: AGPL-3.0-only
"""Write a 20 mm cube as an ASCII STL. Usage: gen_cube.py <out.stl>"""
import sys

S = 20.0
V = [(0, 0, 0), (S, 0, 0), (S, S, 0), (0, S, 0), (0, 0, S), (S, 0, S), (S, S, S), (0, S, S)]
# outward-facing CCW triangles
F = [
    (0, 3, 2), (0, 2, 1),  # bottom (-z)
    (4, 5, 6), (4, 6, 7),  # top (+z)
    (0, 1, 5), (0, 5, 4),  # front (-y)
    (2, 3, 7), (2, 7, 6),  # back (+y)
    (1, 2, 6), (1, 6, 5),  # right (+x)
    (0, 4, 7), (0, 7, 3),  # left (-x)
]


def normal(a, b, c):
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - a[i] for i in range(3)]
    n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
    ln = sum(x * x for x in n) ** 0.5
    return [x / ln for x in n]


with open(sys.argv[1], "w", newline="\n") as f:
    f.write("solid cube20\n")
    for a, b, c in F:
        n = normal(V[a], V[b], V[c])
        f.write("facet normal %g %g %g\n outer loop\n" % tuple(n))
        for i in (a, b, c):
            f.write("  vertex %g %g %g\n" % V[i])
        f.write(" endloop\nendfacet\n")
    f.write("endsolid cube20\n")
