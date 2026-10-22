"""Explicit numerical DLL port. No Runtime registration or operational permission.

Load once per isolated worker. Hard timeout must terminate that worker, not a
Python thread. Legacy and target use different entrypoints in the same assembly.
"""

import ctypes
import hashlib
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    hash_value,
    read_json,
    require,
)


class CalculationPythonNetKernel:
    def __init__(self, dll: Path, *, expected_dll_sha256: str):
        self.dll_sha256 = hash_value(expected_dll_sha256)
        dll = dll.resolve(strict=True)
        require(
            dll.name == "Inventory.Calculation.dll"
            and hashlib.sha256(dll.read_bytes()).hexdigest() == self.dll_sha256,
            "CALCULATION_DLL_HASH_MISMATCH",
        )
        config = dll.with_suffix(".runtimeconfig.json")
        require(config.is_file(), "CALCULATION_RUNTIME_CONFIG_REQUIRED")
        runtime = read_json(config.read_text())
        require(set(runtime) == {"runtimeOptions"}, "CALCULATION_RUNTIME_CONFIG_UNSUPPORTED")
        options = runtime["runtimeOptions"]
        require(type(options) is dict, "CALCULATION_RUNTIME_CONFIG_UNSUPPORTED")
        require(
            options.get("tfm") == "net8.0"
            and options.get("framework") == {"name": "Microsoft.NETCore.App", "version": "8.0.0"}
            and set(options) <= {"tfm", "framework", "configProperties"}
            and options.get("configProperties", {})
            == {
                "System.Reflection.Metadata.MetadataUpdater.IsSupported": False,
                "System.Runtime.Serialization.EnableUnsafeBinaryFormatterSerialization": False,
            },
            "CALCULATION_RUNTIME_CONFIG_UNSUPPORTED",
        )
        from pythonnet import load

        load("coreclr", runtime_config=str(config))
        import clr

        clr.AddReference(str(dll))
        from Inventory.Calculation import Kernel
        from System import Array, Byte, IntPtr
        from System.Runtime.InteropServices import Marshal
        from System.Threading import CancellationToken

        # CLR caches assemblies by identity. A different path/hash must not be
        # claimed when a previous assembly of that name is already loaded.
        from System import Type

        loaded = Path(
            str(
                Type.GetType(
                    "Inventory.Calculation.Kernel, Inventory.Calculation"
                ).Assembly.Location
            )
        )
        require(
            loaded.resolve() == dll
            and hashlib.sha256(loaded.read_bytes()).hexdigest() == self.dll_sha256,
            "CALCULATION_LOADED_BUILD_MISMATCH",
        )
        self.kernel, self.array, self.pointer, self.marshal = Kernel, Array[Byte], IntPtr, Marshal
        self.token = getattr(CancellationToken, "None")

    def _call(self, method: str, value: dict, *, cancellation_token=None) -> dict:
        source = canonical_json(value).encode()
        require(len(source) <= 8_000_000, "CALCULATION_INPUT_LIMIT")
        data = (ctypes.c_ubyte * len(source)).from_buffer_copy(source)
        managed = self.array(len(source))
        self.marshal.Copy(self.pointer(ctypes.addressof(data)), managed, 0, len(source))
        result = getattr(self.kernel, method)(
            managed, self.token if cancellation_token is None else cancellation_token
        )
        size = int(result.Length)
        require(0 < size <= 128_000_000, "CALCULATION_OUTPUT_LIMIT")
        target = (ctypes.c_ubyte * size)()
        self.marshal.Copy(result, 0, self.pointer(ctypes.addressof(target)), size)
        return read_json(bytes(target).decode(), max_bytes=128_000_000)

    def run_legacy(self, payload: dict) -> dict:
        return self._call("RunLegacy", payload)

    def advance_target(self, quantities: dict) -> dict:
        """Numerical primitive only; does not approve a recommended receipt."""
        return self._call("AdvanceTarget", quantities)
