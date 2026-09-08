from setuptools import find_packages, setup

from Cython.Build import cythonize
from setuptools.extension import Extension
import numpy as np


directives = {"language_level": 3, "boundscheck": False, "wraparound": False}
extensions = [
    Extension("zdx_parallel_vm", ["zdx_parallel_vm.py"], include_dirs=[np.get_include()]),
    Extension("zdx_pixel_memory.codec", ["zdx_pixel_memory/codec.py"], include_dirs=[np.get_include()]),
    Extension("zdx_pixel_memory.store", ["zdx_pixel_memory/store.py"]),
    Extension("zdx_pixel_memory.agent_memory", ["zdx_pixel_memory/agent_memory.py"]),
]


setup(
    name="open-pyxel",
    version="1.0.0",
    description="Parallel Pyxel VM — pixel-native virtual machine (open source)",
    packages=find_packages(exclude=["test*"]),
    py_modules=[
        "zdx_compute", "zdx_compute_core", "zdx_resource_policy", "zdx_capabilities",
        "zdx_worker", "zdx_worker_core", "zdx_artifacts", "zdx_artifacts_core",
        "zdx_tls", "zdx_network", "zdx_auth_pipeline", "zdx_ed25519_signer",
        "zdx_server", "zdx_server_chunked", "zdx_server_gossip_core", "zdx_server_core",
        "zdx_gossip", "zdx_cluster", "zdx_cluster_server", "zdx_state",
    ],
    ext_modules=cythonize(extensions, compiler_directives=directives, annotate=False),
    zip_safe=False,
)
