"""Exercise the LÖVE adapter under system Lua when liblua5.4 is available.

The runtime shim is not a substitute for full LÖVE play testing, but catches
syntax mistakes and checks the exact cross-language FNV/Base64 codec.
"""

import ctypes
import ctypes.util
from pathlib import Path
import unittest


class LoveAdapterLuaTests(unittest.TestCase):
    def test_lua_runtime_harness(self):
        name = ctypes.util.find_library("lua5.4")
        if not name:
            self.skipTest("liblua5.4 unavailable")
        lib = ctypes.CDLL(name)
        lib.luaL_newstate.restype = ctypes.c_void_p
        lib.luaL_openlibs.argtypes = [ctypes.c_void_p]
        lib.luaL_loadfilex.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        lib.luaL_loadfilex.restype = ctypes.c_int
        lib.lua_pcallk.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_longlong, ctypes.c_void_p]
        lib.lua_pcallk.restype = ctypes.c_int
        lib.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        lib.lua_tolstring.restype = ctypes.c_char_p
        lib.lua_close.argtypes = [ctypes.c_void_p]
        state = lib.luaL_newstate()
        self.assertTrue(state)
        try:
            lib.luaL_openlibs(state)
            script = Path(__file__).with_name("love_adapter_harness.lua")
            load = lib.luaL_loadfilex(state, str(script).encode(), None)
            if load:
                self.fail(lib.lua_tolstring(state, -1, None).decode())
            executed = lib.lua_pcallk(state, 0, 0, 0, 0, None)
            if executed:
                self.fail(lib.lua_tolstring(state, -1, None).decode())
        finally:
            lib.lua_close(state)


if __name__ == "__main__":
    unittest.main()
