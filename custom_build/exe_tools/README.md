# exe_tools -- read the code frozen inside the official Client.exe

The launcher sometimes ships a `Client.exe` whose built-in game modules are newer than the
loose `_internal/*.py` it delivers in the same release (seen with v0.5.297: six modules).
The exe runs its frozen copy, so the loose source can be stale while `version.txt` is current.
These scripts read the exe's bytecode (read-only; the exe is never executed) using the same
Python 3.14 as `custom_build/python.exe`. Run them with that interpreter.

Detect stale source
    python compare_frozen_loose.py <Client.exe> <_internal dir>      # which modules differ
    python func_delta.py <Client.exe> <_internal dir> mod1 mod2 ...  # which functions differ

Rebuild a module's official source (do this on a copy of the loose file, then verify)
    python show_func.py <Client.exe> <module> <Qualname> ...         # readable disassembly ('*' = all)
    python get_docs.py <Client.exe> <module>                         # exact docstrings
    python get_const.py <Client.exe> <module> <NAME>                 # a module-level tuple constant
    python verify_port.py <Client.exe> <module> <candidate.py> [Qualname ...]
    python verify_port.py <Client.exe> <module> <candidate.py> --diff Qualname
        # LOOSE=1 in the environment ignores jump targets, to find the first real difference

verify_port compares normalized instruction streams (line numbers ignored, docstrings kept), so
a function counts as done only when it compiles to the official bytecode.
