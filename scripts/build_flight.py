"""Generate flight/ons_explore_refresh/main.py from the template and sql/ files.

Run: uv run python scripts/build_flight.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "flight" / "ons_explore_refresh" / "main.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def build() -> None:
    text = read(ROOT / "flight" / "main.template.py")
    replacements = {
        "@@SQL_00@@": read(ROOT / "sql" / "00_base_tables.sql"),
        "@@SQL_01@@": read(ROOT / "sql" / "01_dim_viz_metadata.sql"),
        "@@SQL_02@@": read(ROOT / "sql" / "02_export_views.sql"),
        "@@CATEGORIES@@": read(ROOT / "sql" / "indicator_categories.csv"),
    }
    for marker, body in replacements.items():
        if '"""' in body:
            raise ValueError(f"{marker} source contains a triple quote")
        text = text.replace(marker, body)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"Wrote {OUT} ({len(text.encode('utf-8')) / 1024:.0f} KB)")


if __name__ == "__main__":
    build()
