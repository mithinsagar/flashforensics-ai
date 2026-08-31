"""Downsamples the captured screenshots to a size a repository should carry.

Shooting at 1.5x and resolving down to 1x keeps the text crisply antialiased
while cutting each file to a fraction of the raw capture, which matters when
eleven of them live in git and get fetched by everyone who clones this.
"""

import sys
from pathlib import Path

from PIL import Image

TARGET_WIDTH = 1500

total_before = total_after = 0
for path in sorted(Path(sys.argv[1] if len(sys.argv) > 1 else "../docs/screens").glob("*.png")):
    before = path.stat().st_size
    image = Image.open(path).convert("RGB")
    if image.width > TARGET_WIDTH:
        height = round(image.height * TARGET_WIDTH / image.width)
        image = image.resize((TARGET_WIDTH, height), Image.LANCZOS)
    # Deliberately not palette-quantised: the verdict and damage swatches carry
    # meaning by hue, and a 256-colour palette merged several of them into the
    # same grey. Full colour costs a few megabytes and keeps the legend honest.
    image.save(path, "PNG", optimize=True)
    after = path.stat().st_size
    total_before += before
    total_after += after
    print(f"  {path.name:28} {before / 1e6:5.2f} MB -> {after / 1e6:5.2f} MB")

print(f"total {total_before / 1e6:.1f} MB -> {total_after / 1e6:.1f} MB")
