import io
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape


NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def column_letter(number):
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def column_index(reference):
    result = 0
    for char in reference:
        if not char.isalpha():
            break
        result = result * 26 + ord(char.upper()) - 64
    return result


def build_workbook(rows, sheet_name="Sheet1"):
    max_row = len(rows)
    max_col = max((len(row) for row in rows), default=1)
    row_xml = []
    for row_number, row in enumerate(rows, 1):
        cells = []
        for col_number, value in enumerate(row, 1):
            if value is None or value == "":
                continue
            reference = f"{column_letter(col_number)}{row_number}"
            if isinstance(value, bool):
                cells.append(f'<c r="{reference}" t="b"><v>{1 if value else 0}</v></c>')
            elif isinstance(value, (int, float)):
                cells.append(f'<c r="{reference}"><v>{value}</v></c>')
            else:
                text = escape(str(value))
                cells.append(
                    f'<c r="{reference}" t="inlineStr">'
                    f'<is><t xml:space="preserve">{text}</t></is></c>'
                )
        row_xml.append(f'<row r="{row_number}">{"".join(cells)}</row>')

    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<worksheet xmlns="{NS_MAIN}">'
        f'<dimension ref="A1:{column_letter(max_col)}{max_row or 1}"/>'
        '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        '<sheetData>' + "".join(row_xml) + "</sheetData></worksheet>"
    ).encode("utf-8")
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<workbook xmlns="{NS_MAIN}" xmlns:r="{NS_REL}">'
        '<sheets>'
        f'<sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/>'
        "</sheets></workbook>"
    ).encode("utf-8")
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Relationships xmlns="{NS_PACKAGE_REL}">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    ).encode("utf-8")
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Relationships xmlns="{NS_PACKAGE_REL}">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    ).encode("utf-8")
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        "</Types>"
    ).encode("utf-8")
    styles = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<styleSheet xmlns="{NS_MAIN}"><fonts count="1"><font/></fonts>'
        '<fills count="1"><fill/></fills><borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
        '<cellXfs count="1"><xf/></cellXfs></styleSheet>'
    ).encode("utf-8")

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def _shared_strings(archive):
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values = []
    for item in root.findall(f"{{{NS_MAIN}}}si"):
        values.append("".join(node.text or "" for node in item.iter(f"{{{NS_MAIN}}}t")))
    return values


def _worksheet_path(archive):
    if "xl/worksheets/sheet1.xml" in archive.namelist():
        return "xl/worksheets/sheet1.xml"
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    sheet = workbook.find(f".//{{{NS_MAIN}}}sheet")
    if sheet is None:
        raise ValueError("Excel文件没有工作表")
    relationship_id = sheet.get(f"{{{NS_REL}}}id")
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for relationship in relationships.findall(f"{{{NS_PACKAGE_REL}}}Relationship"):
        if relationship.get("Id") != relationship_id:
            continue
        target = relationship.get("Target", "").lstrip("/")
        if target.startswith("xl/"):
            return target
        return f"xl/{target}"
    raise ValueError("Excel工作表关系不存在")


def _cell_value(cell, shared):
    cell_type = cell.get("t")
    if cell_type == "inlineStr":
        inline = cell.find(f"{{{NS_MAIN}}}is")
        if inline is None:
            return ""
        return "".join(node.text or "" for node in inline.iter(f"{{{NS_MAIN}}}t"))

    value_node = cell.find(f"{{{NS_MAIN}}}v")
    if value_node is None or value_node.text is None:
        return ""
    raw = value_node.text
    if cell_type == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    if cell_type == "b":
        return "TRUE" if raw == "1" else "FALSE"
    return raw


def read_workbook(data):
    """Read the first worksheet and return rows with Excel row numbers."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            shared = _shared_strings(archive)
            root = ET.fromstring(archive.read(_worksheet_path(archive)))
            rows = []
            for row in root.iter(f"{{{NS_MAIN}}}row"):
                row_number = int(row.get("r", len(rows) + 1))
                values = {}
                for cell in row.findall(f"{{{NS_MAIN}}}c"):
                    index = column_index(cell.get("r", ""))
                    if index:
                        values[index] = _cell_value(cell, shared)
                width = max(values, default=0)
                rows.append(
                    (row_number, [values.get(i, "") for i in range(1, width + 1)])
                )
            return rows
    except (OSError, KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
        raise ValueError("不是有效的xlsx文件") from exc
