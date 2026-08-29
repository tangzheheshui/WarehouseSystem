#!/usr/bin/env python3
"""Run a controlled end-to-end acceptance flow against the acme test site."""

import argparse
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "server"))

import excelio


class Api:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")

    def request(self, method, path, token="", json_body=None, data=None,
                headers=None, expected=(200,)):
        body = None
        request_headers = dict(headers or {})
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        elif data is not None:
            body = data
        if token:
            request_headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                status = response.status
                content = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            content = exc.read()
        if status not in expected:
            error = self.text(content)
            raise AssertionError(f"{method} {path} returned {status}: {error}")
        return status, content

    def json(self, method, path, token="", json_body=None, data=None,
             headers=None, expected=(200,)):
        _, content = self.request(
            method, path, token, json_body, data, headers=headers,
            expected=expected,
        )
        return json.loads(content.decode("utf-8"))

    @staticmethod
    def text(content):
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return repr(content[:200])

    def login(self, username, password):
        result = self.json("POST", "/api/login", json_body={
            "username": username,
            "password": password,
        })
        if not result.get("token"):
            raise AssertionError("login response has no token")
        return result["token"], result["user"]

    def upload(self, path, token, filename, content, project_id):
        boundary = f"----wms-{uuid.uuid4().hex}"
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
        return self.json(
            "POST",
            path,
            token=token,
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "X-Project-Id": str(project_id),
            },
        )


def query(path, **values):
    return f"{path}?{urllib.parse.urlencode(values)}"


def workbook_for(template, rows):
    columns = template["columns"]
    table = [[column["name"] for column in columns]]
    for row in rows:
        table.append([row.get(column["key"], "") for column in columns])
    return excelio.build_workbook(table, f"real-{template['type']}")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def run(args):
    api = Api(args.base_url)
    run_id = time.strftime("%Y%m%d%H%M%S")
    product_code = f"AUTO-{run_id}"

    health = api.json("GET", "/api/health")
    require(health.get("status") == "ok", "health status is not ok")
    require(health.get("basePath") == "/acme", "health basePath is not /acme")

    admin_token, admin = api.login(args.admin_username, args.admin_password)
    project_token, project_user = api.login(
        args.project_username, args.project_password
    )
    require(admin["role"] == "admin", "admin role is incorrect")
    require(project_user["role"] == "project_admin", "project role is incorrect")

    me = api.json(
        "GET", "/api/me", token=project_token,
        headers={"X-Project-Id": ""},
    )
    projects = me.get("projects", [])
    require(len(projects) == 1, "project admin should have exactly one project")
    project_id = projects[0]["id"]

    api.json(
        "GET", "/api/products", token=project_token,
        headers={"X-Project-Id": str(project_id)}, expected=(403,),
    )
    api.json(
        "GET", "/api/templates", token=admin_token,
        headers={"X-Project-Id": str(project_id)}, expected=(403,),
    )

    warehouses = api.json("GET", "/api/warehouses", token=admin_token)
    warehouse = next((
        item for item in warehouses
        if item["status"] == "active" and any(
            location["status"] == "active" for location in item["locations"]
        )
    ), None)
    require(warehouse is not None, "no active warehouse/location exists")
    location = next(
        item for item in warehouse["locations"] if item["status"] == "active"
    )

    product = api.json("POST", "/api/products", token=admin_token, json_body={
        "productCode": product_code,
        "productName": f"自动化真实测试商品{run_id}",
        "spec": "AUTO-SPEC",
        "unit": "个",
    })
    require(product["product_id"] == product_code, "product was not created")

    templates = api.json(
        "GET", "/api/templates", token=project_token,
        headers={"X-Project-Id": str(project_id)},
    )
    templates_by_type = {item["type"]: item for item in templates}
    require(set(templates_by_type) == {"inbound", "outbound"}, "template list is incomplete")

    downloaded_headers = {}
    for order_type in ("inbound", "outbound"):
        _, content = api.request(
            "GET",
            f"/api/templates/{order_type}/download",
            token=project_token,
            headers={"X-Project-Id": str(project_id)},
        )
        rows = excelio.read_workbook(content)
        expected_headers = [
            column["name"] for column in templates_by_type[order_type]["columns"]
        ]
        require(rows and rows[0][1] == expected_headers, f"{order_type} template headers differ")
        downloaded_headers[order_type] = expected_headers

    shared = {
        "product_code": product_code,
        "product_name": product["product_name"],
        "spec": product["spec"],
        "unit": product["unit"],
        "warehouse_name": warehouse["warehouse_name"],
        "storage_area": location["storage_area"],
        "cargo_area": location["cargo_area"],
        "location_name": location["location_name"],
        "quality_status": "良品",
        "purchase_order_no": f"AUTO-PO-{run_id}",
    }
    inbound_rows = [
        {
            **shared,
            "batch_no": f"AUTO-B1-{run_id}",
            "production_date": "2026-08-01",
            "shelf_life_days": 30,
            "inbound_date": "2026-08-20",
            "quantity": 5,
        },
        {
            **shared,
            "batch_no": f"AUTO-B2-{run_id}",
            "production_date": "2026-08-05",
            "shelf_life_days": 30,
            "inbound_date": "2026-08-21",
            "quantity": 4,
        },
    ]
    inbound_book = workbook_for(templates_by_type["inbound"], inbound_rows)
    inbound = api.upload(
        "/api/inbound/upload", project_token,
        f"auto-inbound-{run_id}.xlsx", inbound_book, project_id,
    )
    require(inbound["status"] == "completed", "inbound order is not completed")
    require(
        {item["batchNo"]: item["quantity"] for item in inbound["aggregated"]}
        == {f"AUTO-B1-{run_id}": 5, f"AUTO-B2-{run_id}": 4},
        "inbound aggregation is incorrect",
    )

    inventory = api.json(
        "GET", query("/api/inventory", productCode=product_code),
        token=project_token,
        headers={"X-Project-Id": str(project_id)},
    )
    quantities = {item["batch_no"]: item["total_quantity"] for item in inventory}
    require(quantities == {f"AUTO-B1-{run_id}": 5, f"AUTO-B2-{run_id}": 4},
            "inventory quantities after inbound are incorrect")
    expiry = {item["batch_no"]: item["expiry_date"] for item in inventory}
    require(expiry[f"AUTO-B1-{run_id}"] == "2026-08-31", "B1 expiry is incorrect")
    require(expiry[f"AUTO-B2-{run_id}"] == "2026-09-04", "B2 expiry is incorrect")

    outbound_rows = [{
        "product_code": product_code,
        "product_name": product["product_name"],
        "quantity": 7,
        "note": f"自动化出库{run_id}",
    }]
    outbound_book = workbook_for(templates_by_type["outbound"], outbound_rows)
    outbound = api.upload(
        "/api/outbound/upload", project_token,
        f"auto-outbound-{run_id}.xlsx", outbound_book, project_id,
    )
    require(outbound["status"] == "pending_picking", "outbound status is incorrect")
    picking = outbound["pickingOrder"]
    require(picking["status"] == "pending", "picking status is incorrect")
    allocations = {
        item["batch_no"]: item["quantity"] for item in outbound["pickingItems"]
    }
    require(allocations == {f"AUTO-B1-{run_id}": 5, f"AUTO-B2-{run_id}": 2},
            "FIFO allocation is incorrect")

    inventory_after = api.json(
        "GET", query("/api/inventory", productCode=product_code),
        token=project_token,
        headers={"X-Project-Id": str(project_id)},
    )
    remaining = {item["batch_no"]: item["total_quantity"] for item in inventory_after}
    require(remaining == {f"AUTO-B1-{run_id}": 0, f"AUTO-B2-{run_id}": 2},
            "inventory quantities after outbound are incorrect")

    logs = api.json(
        "GET", query("/api/stock-logs", productCode=product_code),
        token=project_token,
        headers={"X-Project-Id": str(project_id)},
    )
    changes = {item["change_quantity"] for item in logs}
    require({5, 4, -5, -2}.issubset(changes), "stock logs are incomplete")

    picking_detail = api.json(
        "GET", f"/api/picking-orders/{picking['id']}", token=project_token,
        headers={"X-Project-Id": str(project_id)},
    )
    completed = api.json(
        "POST", f"/api/picking-orders/{picking['id']}/complete",
        token=project_token,
        headers={"X-Project-Id": str(project_id)},
    )
    require(completed["order"]["status"] == "completed", "picking did not complete")
    require(picking_detail["order"]["status"] == "pending", "pre-complete status was wrong")

    _, inventory_export = api.request(
        "GET", query("/api/inventory/export", productCode=product_code),
        token=project_token,
        headers={"X-Project-Id": str(project_id)},
    )
    exported_inventory = excelio.read_workbook(inventory_export)
    require(len(exported_inventory) == 3, "inventory export row count is incorrect")

    _, picking_export = api.request(
        "GET", f"/api/picking-orders/{picking['id']}/export",
        token=project_token,
        headers={"X-Project-Id": str(project_id)},
    )
    exported_picking = excelio.read_workbook(picking_export)
    require(len(exported_picking) == 3, "picking export row count is incorrect")

    print(json.dumps({
        "result": "passed",
        "baseUrl": args.base_url,
        "projectId": project_id,
        "productCode": product_code,
        "inboundOrderNo": inbound["orderNo"],
        "outboundOrderNo": outbound["orderNo"],
        "pickingNo": picking["pick_no"],
    }, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://193.112.26.217/acme")
    parser.add_argument("--admin-username", default="zhangsan")
    parser.add_argument("--project-username", default="liuhaifeng")
    parser.add_argument("--admin-password", required=True)
    parser.add_argument("--project-password", required=True)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
