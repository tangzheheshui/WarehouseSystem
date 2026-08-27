import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

import excelio


class ExcelTests(unittest.TestCase):
    def test_roundtrip(self):
        rows = [
            ["商品ID", "数量", "生产日期"],
            ["P001", 12, "2026-08-01"],
            ["P002", 1, "2026-08-02"],
        ]
        data = excelio.build_workbook(rows, "测试")
        parsed = excelio.read_workbook(data)
        self.assertEqual(parsed[0], (1, ["商品ID", "数量", "生产日期"]))
        self.assertEqual(parsed[1], (2, ["P001", "12", "2026-08-01"]))
        self.assertEqual(parsed[2], (3, ["P002", "1", "2026-08-02"]))

    def test_blank_cells(self):
        rows = [["A", "B", "C"], ["x", "", "z"]]
        parsed = excelio.read_workbook(excelio.build_workbook(rows))
        self.assertEqual(parsed[1][1], ["x", "", "z"])

    def test_invalid_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.xlsx"
            path.write_bytes(b"not excel")
            with self.assertRaisesRegex(ValueError, "xlsx"):
                excelio.read_workbook(path.read_bytes())


if __name__ == "__main__":
    unittest.main()
