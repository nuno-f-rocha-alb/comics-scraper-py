"""Read and write a per-series notes file (series.xml) inside the series folder."""
import logging
import os
from xml.etree import ElementTree as ET

SERIES_XML_NAME = "series.xml"
FIELDS = ["Description", "Genre", "Characters", "Teams", "Locations", "Notes"]


def empty_fields() -> dict:
    return {k: "" for k in FIELDS}


def _path(series_dir: str) -> str:
    return os.path.join(series_dir, SERIES_XML_NAME)


def read_series_xml(series_dir: str) -> dict:
    path = _path(series_dir)
    if not os.path.isfile(path):
        return empty_fields()
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        logging.warning("series.xml parse error in %s: %s", path, exc)
        return empty_fields()

    out = empty_fields()
    for field in FIELDS:
        node = root.find(field)
        if node is not None and node.text:
            out[field] = node.text.strip()
    return out


def write_series_xml(series_dir: str, fields: dict) -> None:
    os.makedirs(series_dir, exist_ok=True)
    root = ET.Element("SeriesNotes")
    for field in FIELDS:
        el = ET.SubElement(root, field)
        el.text = (fields.get(field) or "").strip() or None
    ET.indent(root, space="  ")
    tree = ET.ElementTree(root)
    tree.write(_path(series_dir), encoding="utf-8", xml_declaration=True)
    logging.info("series.xml written: %s", _path(series_dir))
