"""Lecture des cas de test depuis JSON ou CSV. Partagée par l'API (import) et la CLI (fichier local).

CSV, une ligne par cas, séparateur virgule, point-virgule ou tabulation (détecté) :
- `id` (facultatif), `type` (`qa` par défaut, ou `rag`) ;
- `expected_answer` → `expected.answer` ;
- `context` → passages séparés par `||` ;
- `metadata.<nom>` → `metadata.<nom>` ;
- toute autre colonne → `input.<colonne>` (par exemple `question`).
"""

import csv
import io
import json
from pathlib import Path
from typing import Any

from evalforge_engine.contracts import EvaluationCase

MAX_CASES = 1000
MAX_BYTES = 2 * 1024 * 1024
RESERVED = {"id", "type", "expected_answer", "context"}


class DatasetFormatError(ValueError):
    pass


def parse_json(text: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise DatasetFormatError(f"JSON invalide, ligne {error.lineno} : {error.msg}") from None
    cases = data if isinstance(data, list) else data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, list):
        raise DatasetFormatError('le JSON doit être une liste de cas, ou un objet { "cases": [...] }')
    return _checked(cases)


def parse_csv(text: str) -> list[dict[str, Any]]:
    text = text.removeprefix("﻿")  # BOM ajouté par Excel
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise DatasetFormatError("CSV vide")
    columns = [name.strip() for name in reader.fieldnames]
    if len(set(columns)) != len(columns):
        raise DatasetFormatError("colonnes en double dans l'en-tête")
    if not [c for c in columns if c not in RESERVED and not c.startswith("metadata.")]:
        raise DatasetFormatError("aucune colonne d'entrée (par exemple « question »)")

    cases = []
    for line, row in enumerate(reader, start=2):
        values = {key.strip(): (value or "").strip() for key, value in row.items() if key is not None}
        if not any(values.values()):
            continue  # ligne vide
        case: dict[str, Any] = {"input": {}, "metadata": {}}
        for column, value in values.items():
            if column == "id":
                if value:
                    case["id"] = value
            elif column == "type":
                case["type"] = value or "qa"
            elif column == "expected_answer":
                if value:
                    case["expected"] = {"answer": value}
            elif column == "context":
                case["context"] = [passage.strip() for passage in value.split("||") if passage.strip()]
            elif column.startswith("metadata."):
                if value:
                    case["metadata"][column.removeprefix("metadata.")] = value
            elif value:
                case["input"][column] = value
        if not case["input"]:
            raise DatasetFormatError(f"ligne {line} : aucune valeur d'entrée")
        cases.append(case)
    return _checked(cases)


def _checked(cases: list[Any]) -> list[dict[str, Any]]:
    if not cases:
        raise DatasetFormatError("aucun cas")
    if len(cases) > MAX_CASES:
        raise DatasetFormatError(f"{len(cases)} cas : {MAX_CASES} au plus par version")
    return cases


def parse(text: str, filename: str) -> list[dict[str, Any]]:
    if len(text.encode()) > MAX_BYTES:
        raise DatasetFormatError(f"fichier trop gros : {MAX_BYTES // 1024 // 1024} Mo au plus")
    if filename.lower().endswith(".csv"):
        return parse_csv(text)
    if filename.lower().endswith(".json"):
        return parse_json(text)
    raise DatasetFormatError("format attendu : .json ou .csv")


def load_cases(path: Path) -> list[EvaluationCase]:
    """Pour la CLI : cas validés, identifiants attribués comme le fait l'API (case-001…)."""
    raw = parse(path.read_text(encoding="utf-8"), path.name)
    cases = []
    for position, item in enumerate(raw, start=1):
        try:
            cases.append(EvaluationCase.model_validate({**item, "id": item.get("id") or f"case-{position:03d}"}))
        except ValueError as error:
            raise DatasetFormatError(f"cas {position} invalide : {error}") from None
    ids = [case.id for case in cases]
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        raise DatasetFormatError(f"identifiants de cas en double : {', '.join(duplicates)}")
    return cases
