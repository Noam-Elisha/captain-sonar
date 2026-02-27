# Admiral Radar – Map definitions
# Coordinates are 0-indexed (row, col)

import math
import random

DEFAULT_SETTINGS = {
    "rows": 15,
    "cols": 15,
    "sector_width": 5,
    "sector_height": 5,
    "num_islands": 12,
    "island_size": 2,
}


def generate_map(settings=None):
    """Generate a map definition from settings."""
    s = settings or DEFAULT_SETTINGS
    rows = s["rows"]
    cols = s["cols"]
    sw = s["sector_width"]
    sh = s["sector_height"]

    # Use pre-defined islands from settings if provided (from lobby preview),
    # otherwise generate random ones
    if "islands" in s and s["islands"]:
        islands = [tuple(p) for p in s["islands"]]
    else:
        islands = generate_islands(rows, cols, s.get("num_islands", 12), s.get("island_size", 2))

    return {
        "name": "Custom Map",
        "rows": rows,
        "cols": cols,
        "sector_width": sw,
        "sector_height": sh,
        "sector_size": sw,  # backwards compat
        "islands": islands,
    }


def generate_islands(rows, cols, num_islands, max_island_size):
    """Generate random island positions avoiding edges."""
    island_set = set()
    max_islands = int(rows * cols * 0.1)
    num_islands = min(num_islands, max_islands)

    for _ in range(num_islands):
        attempts = 0
        while attempts < 50:
            r = random.randint(1, rows - 2)
            c = random.randint(1, cols - 2)
            if (r, c) not in island_set:
                break
            attempts += 1
        else:
            continue

        size = 1
        if max_island_size >= 2 and random.random() < 0.15:
            size = random.randint(2, max_island_size)

        for di in range(size):
            for dj in range(size):
                nr, nc = r + di, c + dj
                if 0 < nr < rows - 1 and 0 < nc < cols - 1:
                    if size >= 3 and (di in (0, size-1)) and (dj in (0, size-1)):
                        if random.random() < 0.4:
                            continue
                    island_set.add((nr, nc))

    return sorted(island_set)


def get_sector(row, col, sector_size=5, map_cols=15):
    """Return 1-indexed sector number for a given (row, col)."""
    sectors_per_row = math.ceil(map_cols / sector_size)
    sr = row // sector_size
    sc = col // sector_size
    return sr * sectors_per_row + sc + 1


def get_col_labels(n):
    """Generate A, B, C ... Z, AA, AB ... column labels."""
    labels = []
    for i in range(n):
        if i < 26:
            labels.append(chr(ord('A') + i))
        else:
            labels.append(chr(ord('A') + (i // 26) - 1) + chr(ord('A') + (i % 26)))
    return labels
