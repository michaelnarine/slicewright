# SPDX-License-Identifier: AGPL-3.0-only
# Stand-ins for GUI-side symbols that the headless libslic3r still references (02 section 3.2).
# Fixing a link error here is preferred over another Orca patch.
add_library(slicewright_stubs STATIC
    ColorSpaceConvert_stub.cpp
    nanosvg_impl.cpp
)
target_include_directories(slicewright_stubs PRIVATE ${ORCA_DIR}/src)
