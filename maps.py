# Admiral Radar – Map definitions
# Coordinates are 0-indexed (row, col)
#
# RULEBOOK: "the map is divided into nine sectors in real-time mode and
#            four sectors in turn-by-turn mode."
# This game implements TURN-BY-TURN mode only → 4 sectors (2×2 quadrant layout).
# sector_size=8 on a 15×15 map → ceil(15/8)=2 sectors per axis → 2×2 = 4 sectors.
#   Sector 1: top-left    (rows 0-7,  cols 0-7)
#   Sector 2: top-right   (rows 0-7,  cols 8-14)
#   Sector 3: bottom-left (rows 8-14, cols 0-7)
#   Sector 4: bottom-right(rows 8-14, cols 8-14)

import math

MAPS = {
    "alpha": {
        "name": "Map Alpha",
        "rows": 15,
        "cols": 15,
        "sector_size": 8,   # TBT mode: ceil(15/8)=2 per axis → 2×2 = 4 sectors
        "islands": [
            # Top-left quadrant (rows 0-7, cols 0-7)
            (2, 1), (3, 1),
            (1, 7),
            (6, 2), (7, 2), (7, 3),
            (7, 6),
            # Top-right quadrant (rows 0-7, cols 8-14)
            (0, 12), (1, 12),
            (5, 11), (5, 12),
            # Bottom-left quadrant (rows 8-14, cols 0-7)
            (8, 7),
            (11, 1), (12, 1),
            (10, 7), (11, 7), (11, 8),
            # Bottom-right quadrant (rows 8-14, cols 8-14)
            (12, 11), (13, 12),
        ],
    }
}


def get_sector(row, col, sector_size=8, map_cols=15):
    """Return 1-indexed TBT sector number for a given (row, col).
    RULEBOOK: TBT mode uses 4 sectors (2×2 quadrant layout).
    Uses ceiling division so sector_size=8 on a 15-wide map gives 2 sectors per axis.
    """
    sectors_per_row = math.ceil(map_cols / sector_size)
    sr = row // sector_size
    sc = col // sector_size
    return sr * sectors_per_row + sc + 1


def get_col_labels(n):
    """Generate A, B, C … Z, AA, AB … column labels."""
    labels = []
    for i in range(n):
        if i < 26:
            labels.append(chr(ord('A') + i))
        else:
            labels.append(chr(ord('A') + (i // 26) - 1) + chr(ord('A') + (i % 26)))
    return labels
