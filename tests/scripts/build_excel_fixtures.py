#!/usr/bin/env python3
"""Generate local Excel fixtures for review and manual test uploads."""

import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "server"))

import excelio
import service
from tests.data.warehouse_data import WarehouseDataFactory


OUTPUT_DIR = PROJECT_ROOT / "tests" / "data" / "excel"


def valid_inbound_rows():
    base = {
        "production_date": "2026-08-01",
        "shelf_life_days": 30,
        "inbound_date": "2026-08-20",
        "quality_status": "良品",
        "purchase_order_no": "AUTO-PO-LOCAL",
    }
    return [
        {**base, "batch_no": "AUTO-B001", "quantity": 5},
        {
            **base,
            "batch_no": "AUTO-B002",
            "production_date": "2026-08-05",
            "inbound_date": "2026-08-21",
            "quantity": 4,
        },
    ]


def invalid_inbound_rows():
    return [
        {"product_name": "错误商品名称"},
        {"quantity": 0},
        {"production_date": "2026-8-1", "batch_no": "AUTO-BAD-DATE"},
        {
            "batch_no": "AUTO-BAD-LOCATION",
            "storage_area": "不存在的库区",
            "cargo_area": "不存在的货区",
            "location_name": "不存在的货位",
        },
        {"quality_status": "", "batch_no": "AUTO-NO-QUALITY"},
    ]


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        factory = WarehouseDataFactory(Path(directory) / "excel-fixture.db")
        try:
            files = {
                "inbound-template.xlsx": service.template_workbook(
                    factory.connection, "inbound", factory.project_a["id"]
                ),
                "outbound-template.xlsx": service.template_workbook(
                    factory.connection, "outbound", factory.project_a["id"]
                ),
                "inbound-valid.xlsx": factory.workbook(
                    "inbound", valid_inbound_rows()
                ),
                "inbound-invalid.xlsx": factory.workbook(
                    "inbound", invalid_inbound_rows()
                ),
                "outbound-valid.xlsx": factory.workbook("outbound", [
                    {"quantity": 7, "note": "本地出库样例"},
                ]),
                "outbound-invalid.xlsx": factory.workbook("outbound", [
                    {"quantity": 999, "note": "库存不足样例"},
                ]),
            }
        finally:
            factory.close()

    for filename, content in files.items():
        path = OUTPUT_DIR / filename
        path.write_bytes(content)
        rows = excelio.read_workbook(path.read_bytes())
        if not rows or len(rows[0][1]) < 2:
            raise ValueError(f"{filename} is empty or invalid")
        print(path)


if __name__ == "__main__":
    main()
