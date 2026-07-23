from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sam_common.py"
SPEC = importlib.util.spec_from_file_location("sam_common_device", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
sam_common = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sam_common)


class _Backend:
    def __init__(self, available: bool) -> None:
        self.available = available

    def is_available(self) -> bool:
        return self.available


def _fake_torch(*, cuda: bool, mps: bool) -> SimpleNamespace:
    return SimpleNamespace(
        cuda=_Backend(cuda),
        backends=SimpleNamespace(mps=_Backend(mps)),
    )


class SamDeviceSelectionTests(unittest.TestCase):
    def test_auto_prefers_cuda_over_mps(self) -> None:
        torch = _fake_torch(cuda=True, mps=True)
        self.assertEqual(sam_common.select_sam_device(torch, "auto"), "cuda")

    def test_auto_uses_mps_when_cuda_is_unavailable(self) -> None:
        torch = _fake_torch(cuda=False, mps=True)
        self.assertEqual(sam_common.select_sam_device(torch, "auto"), "mps")

    def test_auto_falls_back_to_cpu(self) -> None:
        torch = _fake_torch(cuda=False, mps=False)
        self.assertEqual(sam_common.select_sam_device(torch, "auto"), "cpu")

    def test_explicit_cpu_is_always_allowed(self) -> None:
        torch = _fake_torch(cuda=False, mps=False)
        self.assertEqual(sam_common.select_sam_device(torch, "cpu"), "cpu")

    def test_explicit_unavailable_accelerator_is_rejected(self) -> None:
        torch = _fake_torch(cuda=False, mps=False)
        with self.assertRaisesRegex(RuntimeError, "MPS.*not available"):
            sam_common.select_sam_device(torch, "mps")
        with self.assertRaisesRegex(RuntimeError, "CUDA.*not available"):
            sam_common.select_sam_device(torch, "cuda")

    def test_parser_accepts_explicit_mps_device(self) -> None:
        parser = sam_common.build_parser("graphene")
        args, _ = parser.parse_known_args(["--sam-device", "mps"])
        self.assertEqual(args.sam_device, "mps")


if __name__ == "__main__":
    unittest.main()
