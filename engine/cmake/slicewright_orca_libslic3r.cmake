# SPDX-License-Identifier: AGPL-3.0-only
#
# Builds the patched libslic3r (02 section 3.1): the settings Orca's root CMakeLists.txt would provide,
# the interface targets libslic3r/CMakeLists.txt expects, the in-tree libraries it links, then libslic3r
# itself with SLIC3R_HEADLESS_MINIMAL=ON. Condensed from the Phase 0 spike root (spike/native, 170 lines).
# Needs ORCA_DIR (patched tree) and the dependency prefix on CMAKE_PREFIX_PATH.

if (${CMAKE_VERSION} VERSION_GREATER_EQUAL "4.0")
    # Some of Orca's in-tree CMake projects still declare an old minimum version.
    set(CMAKE_POLICY_VERSION_MINIMUM 3.13 CACHE STRING "" FORCE)
endif ()

if (APPLE AND NOT CMAKE_OSX_DEPLOYMENT_TARGET)
    # Blender 5.1.2 is 11.2 (02 section 3.4); Orca's own default is 12.0.
    set(CMAKE_OSX_DEPLOYMENT_TARGET "11.2" CACHE STRING "" FORCE)
endif ()

include(${ORCA_DIR}/version.inc)   # SoftFever_VERSION

file(STRINGS "${CMAKE_CURRENT_LIST_DIR}/../ORCA_PIN" _pin_commit REGEX "^commit=")
string(REPLACE "commit=" "" _pin_commit "${_pin_commit}")
string(SUBSTRING "${_pin_commit}" 0 7 _pin_short)
set(SLICEWRIGHT_ORCA_COMMIT "${_pin_commit}" CACHE INTERNAL "")

# ---- settings libslic3r/CMakeLists.txt reads --------------------------------------------------------
set(SLIC3R_BUILD_ID "slicewright")
set(SLIC3R_STATIC ON)
set(SLIC3R_GUI OFF)
set(SLIC3R_PCH ON CACHE BOOL "Use precompiled headers")
set(SLIC3R_PROFILE OFF)
set(SLIC3R_ASAN OFF)
set(IS_CROSS_COMPILE FALSE)
set(SLIC3R_HEADLESS_MINIMAL ON CACHE BOOL "" FORCE)   # patch 0001

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
# Symbol hygiene (02 section 3.5): everything we compile is hidden by default.
set(CMAKE_C_VISIBILITY_PRESET hidden)
set(CMAKE_CXX_VISIBILITY_PRESET hidden)
set(CMAKE_VISIBILITY_INLINES_HIDDEN ON)

if (NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif ()

list(APPEND CMAKE_MODULE_PATH ${ORCA_DIR}/cmake/modules)
set(LIBDIR ${ORCA_DIR}/src)
set(LIBDIR_BIN ${CMAKE_BINARY_DIR}/orca_src)
set(SLIC3R_RESOURCES_DIR ${ORCA_DIR}/resources)

add_compile_definitions("BBL_RELEASE_TO_PUBLIC=$<CONFIG:Release>")
add_definitions("-DGIT_COMMIT_HASH=\"${_pin_short}\"")

# ---- compiler flags (condensed from Orca's root CMakeLists.txt) --------------------------------------
if (MSVC)
    add_compile_options(/MP -bigobj /Zi /FS /utf-8 /wd4244 /wd4267 /wd4018 /wd4305)
    add_compile_options(-D_SILENCE_CXX17_ADAPTOR_TYPEDEFS_DEPRECATION_WARNING)
    add_definitions(-D_USE_MATH_DEFINES -D_WIN32 -D_CRT_SECURE_NO_WARNINGS -D_SCL_SECURE_NO_WARNINGS -D_UNICODE -DUNICODE
                    -DBOOST_ALL_NO_LIB -DBOOST_USE_WINAPI_VERSION=0x602 -DBOOST_SYSTEM_USE_UTF8)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>DLL")  # /MD
else ()
    add_compile_options(-fsigned-char -Wall -Wno-reorder -Werror=return-type
        -Wno-unused-function -Wno-unused-variable -Wno-unused-but-set-variable -Wno-unused-label -Wno-unused-local-typedefs
        -Wno-sign-compare -Wno-misleading-indentation -Wno-switch -Wno-ignored-attributes)
    add_compile_definitions("$<IF:$<CONFIG:Debug>,DEBUG,NDEBUG>")
    if (CMAKE_CXX_COMPILER_ID MATCHES "Clang")
        add_compile_options(-Wno-deprecated-declarations)
        include(CheckCXXCompilerFlag)
        check_cxx_compiler_flag(-Wno-error=enum-constexpr-conversion HAS_WNO_ERROR_ENUM_CONSTEXPR_CONV)
        if (HAS_WNO_ERROR_ENUM_CONSTEXPR_CONV)
            add_compile_options(-Wno-error=enum-constexpr-conversion)
        endif ()
    endif ()
    if (CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
        set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -fext-numeric-literals -Wno-unknown-pragmas")
        if (CMAKE_CXX_COMPILER_VERSION VERSION_GREATER 14)
            set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Wno-error=template-id-cdtor")
        endif ()
    endif ()
endif ()
if (APPLE AND CMAKE_CXX_COMPILER_ID STREQUAL "AppleClang" AND CMAKE_CXX_COMPILER_VERSION VERSION_GREATER 15)
    add_compile_definitions(BOOST_NO_CXX98_FUNCTION_BASE _HAS_AUTO_PTR_ETC=0)
endif ()
if (APPLE)
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Werror=partial-availability -Werror=unguarded-availability -Werror=unguarded-availability-new")
endif ()

# Sanitizer audit build (CI job "sanitizers"): Orca has uninitialised members, so we run an AddressSanitizer +
# UBSan build of the module through a short slice (02 section 3.3). Everything we compile is instrumented; the
# prebuilt dependencies are not. Reports are collected, not fatal (UBSAN/ASAN options in the CI job).
option(SLICEWRIGHT_SANITIZE "Build with AddressSanitizer and UBSan" OFF)
if (SLICEWRIGHT_SANITIZE AND NOT MSVC)
    add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer -fno-optimize-sibling-calls -g1)
    add_link_options(-fsanitize=address,undefined)
endif ()

include_directories(SYSTEM ${LIBDIR} ${ORCA_DIR}/deps_src)

# ---- third party, from the dependency prefix on CMAKE_PREFIX_PATH -------------------------------------
if (APPLE)
    # Mono.framework ships png/jpeg headers that shadow ours otherwise.
    set(CMAKE_FIND_FRAMEWORK LAST)
    set(CMAKE_FIND_APPBUNDLE LAST)
endif ()
set(Boost_USE_STATIC_LIBS ON)
set(Boost_NO_SYSTEM_PATHS TRUE)
if (POLICY CMP0167)
    cmake_policy(SET CMP0167 NEW)
endif ()
set(SLICEWRIGHT_BOOST_COMPONENTS system filesystem thread log log_setup locale regex chrono atomic date_time iostreams program_options nowide)
find_package(Boost 1.83.0 REQUIRED COMPONENTS ${SLICEWRIGHT_BOOST_COMPONENTS})
find_package(Eigen3 5.0.1 REQUIRED)
add_library(boost_libs INTERFACE)
add_library(boost_headeronly INTERFACE)
target_include_directories(boost_headeronly SYSTEM INTERFACE ${Boost_INCLUDE_DIRS})
target_link_libraries(boost_libs INTERFACE boost_headeronly ${Boost_LIBRARIES})
# Boost's own CMake config (the deps build) provides imported targets and leaves Boost_LIBRARIES empty.
foreach (_c ${SLICEWRIGHT_BOOST_COMPONENTS})
    if (TARGET Boost::${_c})
        target_link_libraries(boost_libs INTERFACE Boost::${_c})
    endif ()
endforeach ()

# Orca's root defines these helpers; libslic3r's and the deps_src CMake files call them.
function(slic3r_remap_configs targets from_Cfg to_Cfg)
    if (MSVC)
        string(TOUPPER ${from_Cfg} from_CFG)
        foreach (tgt ${targets})
            if (TARGET ${tgt})
                set_target_properties(${tgt} PROPERTIES MAP_IMPORTED_CONFIG_${from_CFG} ${to_Cfg})
            endif ()
        endforeach ()
    endif ()
endfunction()
function(encoding_check)
endfunction()

set(TBB_STATIC 1)
set(TBB_DEBUG 1)
set(CMAKE_MAP_IMPORTED_CONFIG_RELWITHDEBINFO RelWithDebInfo Release "")
find_package(TBB REQUIRED)
find_package(ZLIB REQUIRED)
find_package(EXPAT REQUIRED)
find_package(PNG REQUIRED)
find_package(cereal REQUIRED)
if (NOT TARGET cereal::cereal)
    set_target_properties(cereal PROPERTIES IMPORTED_GLOBAL TRUE)
    add_library(cereal::cereal ALIAS cereal)
else ()
    set_target_properties(cereal::cereal PROPERTIES IMPORTED_GLOBAL TRUE)
endif ()
find_package(NLopt 1.4 REQUIRED)
find_package(libnoise REQUIRED)

# ---- in-tree libraries from deps_src (only what the trimmed libslic3r links; mcut is gone, patch 0006) --
foreach (d agg ankerl earcut fast_float nanosvg nlohmann admesh clipper clipper2 glu-libtess libigl libnest2d miniz qhull qoi semver Shiny)
    add_subdirectory(${ORCA_DIR}/deps_src/${d} deps_src/${d})
endforeach ()
add_subdirectory(${ORCA_DIR}/src/libslic3r libslic3r)
