from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SWEEP = ROOT / "skills" / "nanodevice_flakedetect_align" / "scripts" / "sweep.py"
REFINE = ROOT / "skills" / "nanodevice_flakedetect_align" / "scripts" / "refine.py"


class TestDiffAlignmentTargetCli(unittest.TestCase):
    def test_sweep_accepts_diff_alignment_target_mask(self):
        result = subprocess.run(
            [sys.executable, str(SWEEP), "--help"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        self.assertIn("--align-target-mask", result.stdout)
        self.assertIn("--align-target-image", result.stdout)

    def test_refine_accepts_diff_alignment_target_mask(self):
        result = subprocess.run(
            [sys.executable, str(REFINE), "--help"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        self.assertIn("--align-target-mask", result.stdout)
        self.assertIn("--align-target-image", result.stdout)


if __name__ == "__main__":
    unittest.main()
