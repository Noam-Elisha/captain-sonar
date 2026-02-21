# Captain Sonar – Map definitions
# Coordinates are 0-indexed (row, col)

MAPS = {
    "alpha": {
        "name": "Map Alpha",
        "rows": 15,
        "cols": 15,
        "sector_size": 5,   # 5×5 sectors → 3×3 = 9 sectors
        "islands": [
            # Sector 1 (rows 0-4, cols 0-4)
            (2, 1), (3, 1),
            # Sector 2 (rows 0-4, cols 5-9)
            (1, 7),
            # Sector 3 (rows 0-4, cols 10-14)
            (0, 12), (1, 12),
            # Sector 4 (rows 5-9, cols 0-4)
            (6, 2), (7, 2), (7, 3),
            # Sector 5 (rows 5-9, cols 5-9)
            (7, 6), (8, 7),
            # Sector 6 (rows 5-9, cols 10-14)
            (5, 11), (5, 12),
            # Sector 7 (rows 10-14, cols 0-4)
            (11, 1), (12, 1),
            # Sector 8 (rows 10-14, cols 5-9)
            (10, 7), (11, 7), (11, 8),
            # Sector 9 (rows 10-14, cols 10-14)
            (12, 11), (13, 12),
        ],
    }
}


def get_sector(row, col, sector_size=5, map_cols=15):
    """Return 1-indexed sector number for a given (row, col)."""
    sectors_per_row = map_cols // sector_size
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
