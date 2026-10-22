"""Opt-in, diagnostic Python.NET adapter; never registered in the operational runtime.

Only the bounded legacy benchmark wire format is supported. DB, policy gates and
the existing Python calculation paths are not imported or changed by this module.
The caller must isolate this in a worker process for hard timeout/cancellation.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import struct
from time import perf_counter

HEADER = struct.Struct("<8s32siiii")
ROW_BYTES = 132
MAX_INPUT_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = HEADER.size + 16 * 32 * 52 * ROW_BYTES


def verify_payload(payload: bytes, source_hash: str, items: int) -> str:
    """Check binding, dimensions and full payload digest, not numerical replay."""
    if type(items) is not int or not 1 <= items <= 16:
        raise ValueError("PYTHONNET_ITEM_COUNT")
    if not re.fullmatch(r"[0-9a-f]{64}", source_hash):
        raise ValueError("PYTHONNET_SOURCE_HASH")
    expected_rows = items * 32 * 52
    if not isinstance(payload, bytes) or len(payload) != HEADER.size + expected_rows * ROW_BYTES:
        raise ValueError("PYTHONNET_PAYLOAD_SIZE")
    if HEADER.unpack_from(payload) != (
        b"BPSI0001",
        bytes.fromhex(source_hash),
        expected_rows,
        items,
        32,
        52,
    ):
        raise ValueError("PYTHONNET_PAYLOAD_BINDING")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class LegacyDotnetResult:
    payload: bytes
    sha256: str
    input_copy_seconds: float
    prepare_seconds: float
    managed_call_seconds: float
    compute_seconds: float
    serialize_seconds: float
    output_copy_seconds: float
    verify_seconds: float
    total_seconds: float


class LegacyPythonNetAdapter:
    """A batch crossing per call; no Python callbacks or per-row CLR iteration."""

    def __init__(self, dll: Path, *, expected_dll_sha256: str) -> None:
        dll = dll.resolve(strict=True)
        if not re.fullmatch(r"[0-9a-f]{64}", expected_dll_sha256):
            raise ValueError("PYTHONNET_DLL_HASH")
        if hashlib.sha256(dll.read_bytes()).hexdigest() != expected_dll_sha256:
            raise ValueError("PYTHONNET_DLL_HASH_MISMATCH")
        config = dll.with_suffix(".runtimeconfig.json")
        if not config.is_file():
            raise ValueError("PYTHONNET_RUNTIME_CONFIG_MISSING")
        try:
            from pythonnet import load
        except ImportError as error:
            raise RuntimeError("PYTHONNET_EXPERIMENT_DEPENDENCY_NOT_INSTALLED") from error

        load("coreclr", runtime_config=str(config))
        import clr

        clr.AddReference(str(dll))
        from Inventory.Legacy import PsiKernel
        from System import Array, Byte, IntPtr
        from System.Runtime.InteropServices import Marshal
        from System.Threading import CancellationToken

        self.kernel = PsiKernel
        self.array = Array[Byte]
        self.pointer = IntPtr
        self.marshal = Marshal
        self.token = getattr(CancellationToken, "None")

    def _to_managed(self, source: bytes):
        data = (ctypes.c_ubyte * len(source)).from_buffer_copy(source)
        managed = self.array(len(source))
        self.marshal.Copy(self.pointer(ctypes.addressof(data)), managed, 0, len(source))
        return managed

    def _to_python(self, managed) -> bytes:
        size = int(managed.Length)
        if not HEADER.size <= size <= MAX_OUTPUT_BYTES:
            raise ValueError("PYTHONNET_OUTPUT_LIMIT")
        data = (ctypes.c_ubyte * size)()
        self.marshal.Copy(managed, 0, self.pointer(ctypes.addressof(data)), size)
        return bytes(data)

    def prepare(self, source: bytes, items: int, *, expected_source_sha256: str):
        if not isinstance(source, bytes) or not 0 < len(source) <= MAX_INPUT_BYTES:
            raise ValueError("PYTHONNET_INPUT_LIMIT")
        if type(items) is not int or not 1 <= items <= 16:
            raise ValueError("PYTHONNET_ITEM_COUNT")
        if hashlib.sha256(source).hexdigest() != expected_source_sha256:
            raise ValueError("PYTHONNET_INPUT_HASH_MISMATCH")
        return self.kernel.Prepare(self._to_managed(source), items)

    def run(self, source: bytes, items: int, *, expected_source_sha256: str) -> LegacyDotnetResult:
        begin = perf_counter()
        if not isinstance(source, bytes) or not 0 < len(source) <= MAX_INPUT_BYTES:
            raise ValueError("PYTHONNET_INPUT_LIMIT")
        if type(items) is not int or not 1 <= items <= 16:
            raise ValueError("PYTHONNET_ITEM_COUNT")
        if hashlib.sha256(source).hexdigest() != expected_source_sha256:
            raise ValueError("PYTHONNET_INPUT_HASH_MISMATCH")
        managed = self._to_managed(source)
        converted = perf_counter()
        batch = self.kernel.Prepare(managed, items)
        prepared = perf_counter()
        result = batch.Run(self.token)
        called = perf_counter()
        payload = self._to_python(result.Payload)
        copied = perf_counter()
        digest = verify_payload(payload, expected_source_sha256, items)
        end = perf_counter()
        return LegacyDotnetResult(
            payload=payload,
            sha256=digest,
            input_copy_seconds=converted - begin,
            prepare_seconds=prepared - converted,
            managed_call_seconds=called - prepared,
            compute_seconds=float(result.ComputeSeconds),
            serialize_seconds=float(result.SerializeSeconds),
            output_copy_seconds=copied - called,
            verify_seconds=end - copied,
            total_seconds=end - begin,
        )
