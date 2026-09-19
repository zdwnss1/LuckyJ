"""Explicit opt-in local C accelerator, no downloads and no runtime dependencies."""
from pathlib import Path
import ctypes
import os
import platform
import shutil
import subprocess

LIB = Path(__file__).parent/'native'/('shanten.dylib' if platform.system()=='Darwin' else 'shanten.so')

def compile_native():
    if platform.system() not in ('Linux','Darwin'):
        raise RuntimeError('Native build supports Linux/macOS; Python fallback works on Windows.')
    cc=shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
    if not cc:raise RuntimeError('No C compiler. Continue with the standard-library Python fallback.')
    temp=LIB.with_name(LIB.name+'.tmp')
    try:
        subprocess.run([cc,'-O3','-shared','-fPIC',str(LIB.with_name('shanten.c')),'-o',str(temp)],check=True,timeout=60)
        os.replace(temp,LIB)
    finally:temp.unlink(missing_ok=True)
    return LIB

def load():
    if os.environ.get('LUCKYJ_PURE_PYTHON')=='1' or not LIB.exists():return None
    library=ctypes.CDLL(str(LIB))
    function=library.luckyj_regular
    function.argtypes=[ctypes.c_char_p];function.restype=ctypes.c_int
    return function
