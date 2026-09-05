from __future__ import annotations

import io
import re
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from .errors import ValidationError
from .util import atomic_write_bytes, formula_safe


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CELL_REF = re.compile(r"^([A-Z]+)([0-9]+)$")
INVALID_XML = re.compile("[\x00-\x08\x0B\x0C\x0E-\x1F]")
MAX_UNCOMPRESSED = 100 * 1024 * 1024


def _column_index(reference: str) -> int:
    match = CELL_REF.match(reference)
    if not match:
        raise ValidationError(f"invalid XLSX cell reference: {reference}")
    result = 0
    for char in match.group(1):
        result = result * 26 + ord(char) - 64
    return result - 1


def _column_name(index: int) -> str:
    result = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _xml_text(value: Any) -> str:
    return INVALID_XML.sub("�", str(value))


def _check_archive(archive: zipfile.ZipFile) -> None:
    infos = archive.infolist()
    if len(infos) > 2000:
        raise ValidationError("XLSX contains too many archive members")
    total = sum(info.file_size for info in infos)
    if total > MAX_UNCOMPRESSED:
        raise ValidationError("XLSX uncompressed content exceeds 100 MiB limit")
    for info in infos:
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or "../" in f"/{name}":
            raise ValidationError("unsafe path inside XLSX archive")


def read_xlsx(path: Path) -> list[dict[str, str]]:
    """Read the first worksheet as a header-oriented table.

    Values are deliberately returned as text; canonical typing is applied by
    the mapping layer. Formula cells are imported as their literal formula
    (with a leading '=') instead of trusting a cached result.
    """

    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValidationError(f"invalid XLSX file {path}: {exc}") from exc
    with archive:
        _check_archive(archive)
        try:
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        except (KeyError, ET.ParseError) as exc:
            raise ValidationError(f"XLSX workbook structure is invalid: {path}") from exc

        rel_targets = {
            node.attrib.get("Id"): node.attrib.get("Target", "")
            for node in relationships.findall(f"{{{PKG_REL_NS}}}Relationship")
        }
        sheet = workbook.find(f"{{{MAIN_NS}}}sheets/{{{MAIN_NS}}}sheet")
        if sheet is None:
            raise ValidationError(f"XLSX has no worksheets: {path}")
        relation_id = sheet.attrib.get(f"{{{REL_NS}}}id")
        target = rel_targets.get(relation_id, "")
        if not target:
            raise ValidationError(f"XLSX worksheet relationship is missing: {path}")
        if target.startswith("/"):
            sheet_path = target.lstrip("/")
        elif target.startswith("xl/"):
            sheet_path = target
        else:
            sheet_path = "xl/" + target.lstrip("/")
        parts: list[str] = []
        for part in sheet_path.split("/"):
            if part == "..":
                if parts:
                    parts.pop()
            elif part not in ("", "."):
                parts.append(part)
        sheet_path = "/".join(parts)

        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            try:
                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                for item in root.findall(f"{{{MAIN_NS}}}si"):
                    shared.append("".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t")))
            except ET.ParseError as exc:
                raise ValidationError(f"XLSX shared strings are invalid: {path}") from exc
        try:
            root = ET.fromstring(archive.read(sheet_path))
        except (KeyError, ET.ParseError) as exc:
            raise ValidationError(f"XLSX first worksheet is invalid: {path}") from exc

        matrix: list[list[str]] = []
        for row_node in root.findall(f".//{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row"):
            cells: dict[int, str] = {}
            for cell in row_node.findall(f"{{{MAIN_NS}}}c"):
                ref = cell.attrib.get("r", "")
                index = _column_index(ref)
                formula = cell.find(f"{{{MAIN_NS}}}f")
                if formula is not None:
                    value = "=" + (formula.text or "")
                elif cell.attrib.get("t") == "inlineStr":
                    value = "".join(node.text or "" for node in cell.iter(f"{{{MAIN_NS}}}t"))
                else:
                    value_node = cell.find(f"{{{MAIN_NS}}}v")
                    value = "" if value_node is None else (value_node.text or "")
                    if cell.attrib.get("t") == "s" and value:
                        try:
                            value = shared[int(value)]
                        except (ValueError, IndexError) as exc:
                            raise ValidationError(f"XLSX shared string index is invalid: {path}") from exc
                    elif cell.attrib.get("t") == "b":
                        value = "true" if value == "1" else "false"
                cells[index] = value
            if cells:
                width = max(cells) + 1
                matrix.append([cells.get(index, "") for index in range(width)])
        if not matrix:
            raise ValidationError(f"XLSX contains no table rows: {path}")
        headers = [value.strip() for value in matrix[0]]
        _validate_headers(headers, path)
        records: list[dict[str, str]] = []
        for row_number, values in enumerate(matrix[1:], start=2):
            if len(values) > len(headers) and any(values[len(headers) :]):
                raise ValidationError(f"XLSX row {row_number} has cells beyond the header")
            padded = values + [""] * (len(headers) - len(values))
            records.append(dict(zip(headers, padded[: len(headers)])))
        return records


def _validate_headers(headers: list[str], path: Path | str = "table") -> None:
    if not headers or any(not header for header in headers):
        raise ValidationError(f"empty column header in {path}")
    duplicates = sorted({header for header in headers if headers.count(header) > 1})
    if duplicates:
        raise ValidationError(f"duplicate column headers in {path}: {', '.join(duplicates)}")


def _sheet_xml(headers: list[str], rows: Iterable[dict[str, Any]]) -> bytes:
    worksheet = ET.Element("worksheet", xmlns=MAIN_NS)
    sheet_data = ET.SubElement(worksheet, "sheetData")
    all_rows = [dict(zip(headers, headers)), *rows]
    for row_index, row in enumerate(all_rows, start=1):
        row_node = ET.SubElement(sheet_data, "row", r=str(row_index))
        for column_index, header in enumerate(headers):
            raw = header if row_index == 1 else row.get(header, "")
            value = formula_safe(raw)
            ref = f"{_column_name(column_index)}{row_index}"
            if isinstance(value, bool):
                cell = ET.SubElement(row_node, "c", r=ref, t="inlineStr")
                inline = ET.SubElement(cell, "is")
                ET.SubElement(inline, "t").text = "true" if value else "false"
            elif isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
                cell = ET.SubElement(row_node, "c", r=ref, t="n")
                ET.SubElement(cell, "v").text = format(value, "f") if isinstance(value, Decimal) else str(value)
            else:
                cell = ET.SubElement(row_node, "c", r=ref, t="inlineStr")
                inline = ET.SubElement(cell, "is")
                text = ET.SubElement(inline, "t")
                string = _xml_text("" if value is None else value)
                if string.startswith(" ") or string.endswith(" "):
                    text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                text.text = string
    return ET.tostring(worksheet, encoding="utf-8", xml_declaration=True)


def write_xlsx(path: Path, sheets: list[tuple[str, list[str], list[dict[str, Any]]]]) -> None:
    if not sheets:
        raise ValueError("at least one worksheet is required")
    seen: set[str] = set()
    for name, headers, _ in sheets:
        if not name or len(name) > 31 or any(char in name for char in "[]:*?/\\") or name in seen:
            raise ValueError(f"invalid or duplicate worksheet name: {name!r}")
        _validate_headers(headers, name)
        seen.add(name)

    content_types = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
                     '<Default Extension="xml" ContentType="application/xml"/>',
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
                     '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
    for index in range(1, len(sheets) + 1):
        content_types.append(f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    content_types.append("</Types>")

    workbook = ET.Element("workbook", xmlns=MAIN_NS)
    workbook.set("xmlns:r", REL_NS)
    sheets_node = ET.SubElement(workbook, "sheets")
    for index, (name, _, _) in enumerate(sheets, start=1):
        sheet = ET.SubElement(sheets_node, "sheet", name=name, sheetId=str(index))
        sheet.set(f"{{{REL_NS}}}id", f"rId{index}")

    relationships = ET.Element("Relationships", xmlns=PKG_REL_NS)
    for index in range(1, len(sheets) + 1):
        ET.SubElement(
            relationships,
            "Relationship",
            Id=f"rId{index}",
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet",
            Target=f"worksheets/sheet{index}.xml",
        )
    ET.SubElement(
        relationships,
        "Relationship",
        Id=f"rId{len(sheets) + 1}",
        Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles",
        Target="styles.xml",
    )
    root_relationships = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''
    styles = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="1"><font><sz val="11"/><name val="Aptos"/></font></fonts>
<fills count="1"><fill><patternFill patternType="none"/></fill></fills>
<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs>
<cellXfs count="1"><xf xfId="0"/></cellXfs>
</styleSheet>'''

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        entries: list[tuple[str, bytes]] = [
            ("[Content_Types].xml", "".join(content_types).encode("utf-8")),
            ("_rels/.rels", root_relationships),
            ("xl/workbook.xml", ET.tostring(workbook, encoding="utf-8", xml_declaration=True)),
            ("xl/_rels/workbook.xml.rels", ET.tostring(relationships, encoding="utf-8", xml_declaration=True)),
            ("xl/styles.xml", styles),
        ]
        for index, (_, headers, rows) in enumerate(sheets, start=1):
            entries.append((f"xl/worksheets/sheet{index}.xml", _sheet_xml(headers, rows)))
        for name, data in entries:
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    atomic_write_bytes(path, buffer.getvalue())

