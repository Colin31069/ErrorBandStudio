"""Generate deterministic synthetic replicates for checking the plotting workflow."""
from __future__ import annotations

import argparse
import math
import random
from pathlib import Path


def create_demo_data(destination: Path) -> None:
    from openpyxl import Workbook

    random_source = random.Random(20261013)
    for label, response, spread in (("2 uL", 1.02, .06), ("3 uL", 1.13, .08), ("4 uL", 1.16, .32), ("5 uL", 1.04, .20)):
        folder = destination / label
        folder.mkdir(parents=True, exist_ok=True)
        for replicate in range(1, 6):
            path = folder / f"{replicate}.xlsx"
            if path.exists():
                continue
            workbook = Workbook(write_only=True)
            sheet = workbook.create_sheet("Synthetic data")
            sheet.append(["pF - Plot 0"])
            baseline = 18. + replicate * .7
            amplitude = response + (replicate - 3) * spread / math.sqrt(2.5)
            onset = 25. + (replicate - 3) * .1
            for index in range(4251):
                elapsed = index * .1 - onset
                change = 0. if elapsed < 0 else amplitude * (1. - math.exp(-elapsed / 3.)) + .0001 * elapsed
                sheet.append([baseline + change + random_source.gauss(0., .002)])
            workbook.save(path)
            workbook.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", nargs="?", type=Path, default=Path(__file__).resolve().parent / "ExampleData")
    create_demo_data(parser.parse_args().destination)
