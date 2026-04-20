"""
setup.py — Cython build configuration for open-pyxel.

Build compiled extensions:
    python setup.py build_ext --inplace

Compiled modules:
    zdx_parallel_vm         (uses numpy)
    zdx_pixel_memory.codec  (uses numpy)
    zdx_pixel_memory.store
    zdx_pixel_memory.agent_memory

Stay as pure Python:
    pyxel_registry
    zdx_agent_runtime

On Linux/Mac:  output is <module>.cpython-3x-<arch>.so
On Windows:    output is <module>.cpython-3x-<arch>.pyd
"""

from setuptools import setup, find_packages
from Cython.Build import cythonize
from setuptools.extension import Extension
import numpy as np

_COMPILER_DIRECTIVES = {
    "language_level": 3,
    "boundscheck": False,
    "wraparound": False,
}

_NUMPY_INC = [np.get_include()]

extensions = [
    Extension(
        name="zdx_parallel_vm",
        sources=["zdx_parallel_vm.py"],
        include_dirs=_NUMPY_INC,
    ),
    Extension(
        name="zdx_pixel_memory.codec",
        sources=["zdx_pixel_memory/codec.py"],
        include_dirs=_NUMPY_INC,
    ),
    Extension(
        name="zdx_pixel_memory.store",
        sources=["zdx_pixel_memory/store.py"],
    ),
    Extension(
        name="zdx_pixel_memory.agent_memory",
        sources=["zdx_pixel_memory/agent_memory.py"],
    ),
]

setup(
    name="open-pyxel",
    version="1.0.0",
    description="Parallel Pyxel VM — pixel-native virtual machine (open source)",
    packages=find_packages(exclude=["test*"]),
    ext_modules=cythonize(
        extensions,
        compiler_directives=_COMPILER_DIRECTIVES,
        annotate=False,
    ),
    zip_safe=False,
)
