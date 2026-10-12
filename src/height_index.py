"""Numeric geometry for a flat virtual view. No Qt or text layout work here."""


def layout_identity(row):
    kind = row.get("kind", "ENTRY")
    if kind not in ("ENTRY", "DISABLED_ENTRY"):
        return (kind, row.get("element_id") or (str(row.get("file", "")), row.get("line")))
    if row.get("entry_id"):
        return ("ENTRY", row["entry_id"])
    return ("REFERENCE_ENTRY", row.get("section"),
            str(row.get("new_file") or row.get("old_file") or ""),
            row.get("new_line") or row.get("old_line"), row.get("key"), row.get("position"))


class HeightIndex:
    """Fenwick sums of positive estimated/measured heights, indexed in proxy order."""

    def __init__(self, identities=(), heights=()):
        self.identities = list(identities)
        self.heights = [max(1, int(h)) for h in heights]
        self.positions = {identity: i for i, identity in enumerate(self.identities)}
        self.tree = [0] + self.heights.copy()
        for i in range(1, len(self.tree)):
            parent = i + (i & -i)
            if parent < len(self.tree):
                self.tree[parent] += self.tree[i]

    def __len__(self):
        return len(self.heights)

    def prefix(self, end):
        """Height before row end (exclusive)."""
        end = max(0, min(end, len(self)))
        result = 0
        while end:
            result += self.tree[end]
            end -= end & -end
        return result

    @property
    def total(self):
        return self.prefix(len(self))

    def update(self, row, height):
        height = max(1, int(height))
        delta = height - self.heights[row]
        self.heights[row] = height
        i = row + 1
        while i < len(self.tree):
            self.tree[i] += delta
            i += i & -i
        return delta

    def row_at(self, y):
        if not self.heights:
            return -1
        y = max(0, min(int(y), self.total - 1))
        row = 0
        bit = 1 << (len(self).bit_length() - 1)
        while bit:
            next_row = row + bit
            if next_row <= len(self) and self.tree[next_row] <= y:
                y -= self.tree[next_row]
                row = next_row
            bit >>= 1
        return min(row, len(self) - 1)
