from pathlib import Path
import polars as pl


def read_parquet_or_empty(path: Path, schema: dict | None = None) -> pl.DataFrame:
    if path.exists():
        return pl.read_parquet(path)
    if schema:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame()


def write_parquet(df: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path, compression="zstd")
