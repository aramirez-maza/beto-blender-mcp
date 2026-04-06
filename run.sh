#!/bin/bash
# BETO-TRACE: BLENDERFACE.SEC1.INTENT.PIPELINE_FOTO_A_3D
# Wrapper que setea LD_LIBRARY_PATH antes de lanzar Python
# (pytorch3d necesita libc10.so de torch)

TORCH_LIB=$(python3 -c "import torch, os; print(os.path.join(os.path.dirname(torch.__file__), 'lib'))")
export LD_LIBRARY_PATH="$TORCH_LIB:$LD_LIBRARY_PATH"

cd "$(dirname "$0")"
python3 main.py "$@"
