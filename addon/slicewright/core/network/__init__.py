# SPDX-License-Identifier: GPL-3.0-or-later
"""Network clients for sending G-code to printers (03 section 8). Pure Python, standard library only, no ``bpy``, no
threads: every operation is a generator advanced in small steps (see :mod:`~slicewright.core.network.nbsocket`)."""
