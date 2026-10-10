"""Full result tables as downloadable files (CSV and Parquet), content-addressed and immutable."""

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from geomcp_core.config import data_dir

FORMATS = ("csv", "parquet")


def files_dir() -> Path:
    return data_dir() / "files"


def write_table(columns: list[str], rows: list[list[Any]], stem: str) -> dict[str, Path]:
    """Write the table as CSV and Parquet; the file names are the SHA-256 of the content, so repeats dedupe."""
    digest = hashlib.sha256(json.dumps([columns, rows], default=str).encode()).hexdigest()[:32]
    out: dict[str, Path] = {}
    files_dir().mkdir(parents=True, exist_ok=True)
    csv_path = files_dir() / f"{stem}-{digest}.csv"
    if not csv_path.exists():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        writer.writerows([["" if v is None else v for v in row] for row in rows])
        csv_path.write_text(buf.getvalue())
    out["csv"] = csv_path
    parquet_path = files_dir() / f"{stem}-{digest}.parquet"
    if not parquet_path.exists():
        table = pa.table({c: [r[i] for r in rows] for i, c in enumerate(columns)})
        pq.write_table(table, parquet_path)  # pyright: ignore[reportUnknownMemberType]  (stubs lack a full signature)
    out["parquet"] = parquet_path
    return out
