"""Regenerate TOOL_EXAMPLES.md from real tool calls, so the contract never drifts from the code.

Run with real credentials in .env:  uv run python scripts/make_tool_examples.py
It uses the normal cache, so a rerun costs almost nothing.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastmcp import Client
from mcp.types import ImageContent, TextContent

load_dotenv()

from geomcp_server.app import build_server  # noqa: E402  (after .env is loaded)
from geomcp_server.settings import Settings  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "TOOL_EXAMPLES.md"
MAX_LIST = 4


def _trim(value: Any, depth: int = 0) -> Any:
    """Keep examples readable: shorten long lists and geometries."""
    if isinstance(value, dict):
        if value.get("type") in ("Polygon", "MultiPolygon") and "coordinates" in value:
            return {"type": value["type"], "coordinates": "... (simplified to at most ~200 vertices)"}
        return {k: _trim(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        if len(value) > MAX_LIST and depth > 0:
            return [_trim(v, depth + 1) for v in value[:MAX_LIST]] + [f"... {len(value) - MAX_LIST} more"]
        return [_trim(v, depth + 1) for v in value]
    return value


def _block(obj: Any) -> str:
    return "```json\n" + json.dumps(_trim(obj), ensure_ascii=False, indent=2) + "\n```"


def _redact_traders(content: dict[str, Any]) -> dict[str, Any]:
    redacted = {"name": "<name>", "nip": "<nip>", "regon": "<regon>", "address": "<address>"}
    return content | {"listed": [t | redacted for t in content.get("listed", [])]}


async def _call(c: Client, title: str, tool: str, args: dict[str, Any], out: list[str]) -> dict[str, Any]:
    r = await c.call_tool(tool, args, raise_on_error=False)
    text = " ".join(b.text for b in r.content if isinstance(b, TextContent))
    if tool == "find_businesses":  # sole traders are people: keep their names and addresses out of the docs
        r.structured_content = _redact_traders(r.structured_content or {})
        text = "\n".join(
            line if not line.startswith("- ") else "- <name>, <address> (NIP <nip>, <status>, since <date>)"
            for line in text.splitlines()
        )
    images = [b for b in r.content if isinstance(b, ImageContent)]
    out += [f"### {title}", "", "Call:", "", _block({"tool": tool, "arguments": args}), ""]
    if images:
        out += [f"Content: an MCP image block ({images[0].mime_type}) and a text block.", ""]
    out += ["Structured content:", "", _block(r.structured_content), "", "Text:", "", "```", text, "```", ""]
    return r.structured_content or {}


async def main() -> None:
    out = [
        "# geomcp tool examples (contract reference)",
        "",
        "Generated from real tool calls by `scripts/make_tool_examples.py`; do not edit by hand.",
        "Long lists and geometries are shortened (`... N more`). Values are real data for Wrocław and nearby gminas.",
        "",
        "Conventions:",
        "- Every tool returns `structuredContent` plus a short text summary.",
        "- Every value carries units, status (`final`, `preliminary`, `missing`), licence and attribution.",
        "- Expected failures return `isError` with `{error: {code, message, hint}}`.",
        "- Calls that would exceed the sync budget (45 s) return a `job_id`; `get_job` long-polls for up to 20 s.",
        "",
    ]
    async with Client(build_server(settings=Settings())) as c:
        out += ["## resolve_area", ""]
        gm = await _call(c, "A gmina by name", "resolve_area", {"query": "gmina Kąty Wrocławskie"}, out)
        gold = await _call(
            c,
            "Coordinates with a square buffer (the golden AOI)",
            "resolve_area",
            {"query": "51.110,17.032", "buffer_m": 1581, "shape": "square"},
            out,
        )
        await _call(c, "An ambiguous name", "resolve_area", {"query": "Lipno"}, out)
        await _call(c, "A Polish cadastral parcel", "resolve_area", {"query": "026401_1.0001.2395"}, out)
        kob = await _call(c, "Another gmina", "resolve_area", {"query": "gmina Kobierzyce"}, out)
        out += ["## list_data", ""]
        await _call(c, "Weather variables", "list_data", {"topic": "weather"}, out)
        out += ["## get_timeseries", ""]
        await _call(
            c,
            "Ten years, yearly, three variables",
            "get_timeseries",
            {
                "area_id": gm["selected"],
                "variables": ["ndvi", "t2m", "precipitation"],
                "start": "2016-01-01",
                "end": "2026-01-01",
                "cadence": "P1Y",
            },
            out,
        )
        out += ["## get_summary", ""]
        await _call(
            c,
            "Terrain, land, infrastructure, people, recent conditions",
            "get_summary",
            {
                "area_id": gm["selected"],
                "variables": [
                    "elevation_ground",
                    "slope_ground",
                    "landcover",
                    "buildings",
                    "road_length",
                    "schools",
                    "population",
                    "budget_income_per_capita",
                    "ndvi",
                    "t2m",
                    "precipitation",
                    "no2_column",
                    "pm10_station",
                ],
            },
            out,
        )
        await _call(
            c,
            "Population, buildings, land-cover history, solar, climate to 2050 and air history (Smolec)",
            "get_summary",
            {
                "area_id": "a_pt_51.0730_16.8990_ci1000",
                "variables": [
                    "population_grid",
                    "building_functions",
                    "landcover_history",
                    "solar_potential",
                    "climate_projection",
                    "pm10_history",
                ],
            },
            out,
        )
        out += ["## compare_areas", ""]
        await _call(
            c,
            "Two gminas, summer NDVI",
            "compare_areas",
            {
                "area_ids": [gm["selected"], kob["selected"]],
                "variable": "ndvi",
                "start": "2025-06-01",
                "end": "2025-09-01",
            },
            out,
        )
        out += ["## get_snapshot", ""]
        await _call(
            c, "NDVI map", "get_snapshot", {"area_id": gold["selected"], "layer": "ndvi", "date": "2025-07-19"}, out
        )
        await _call(
            c,
            "Aerial photo of a parcel",
            "get_snapshot",
            {"area_id": "a_uldk_026401_1.0001.2395", "layer": "orthophoto"},
            out,
        )
        out += ["## describe_data", ""]
        await _call(
            c,
            "Hazard sources, and which cover an area",
            "describe_data",
            {"topic": "hazard", "area_id": gold["selected"]},
            out,
        )
        out += ["## find_statistics", ""]
        await _call(c, "Search official indicators (Polish words)", "find_statistics", {"query": "wynagrodzenie"}, out)
        out += ["## get_statistics", ""]
        await _call(
            c,
            "Population, compared with powiat, voivodeship and Poland",
            "get_statistics",
            {"area_id": gm["selected"], "indicator_ids": ["bdl:72305"], "years": [2020, 2023]},
            out,
        )
        out += ["## get_constraints", ""]
        await _call(
            c, "Floodplain district in Wrocław", "get_constraints", {"area_id": "a_pt_51.1450_16.9550_ci500"}, out
        )
        out += ["## get_prices", ""]
        await _call(
            c,
            "Old-town apartments since 2025",
            "get_prices",
            {"area_id": "a_pt_51.1080_17.0400_ci200", "since_year": 2025},
            out,
        )
        out += ["## get_zoning", ""]
        await _call(
            c, "A point in Kraków's Kazimierz plan", "get_zoning", {"area_id": "a_pt_50.0510_19.9450_ci50"}, out
        )
        await _call(
            c,
            "A Wrocław parcel (no plan in the national service)",
            "get_zoning",
            {"area_id": "a_uldk_026401_1.0001.2395"},
            out,
        )
        out += ["## get_traffic", ""]
        await _call(
            c, "The A4 south of Kąty Wrocławskie", "get_traffic", {"area_id": "a_pt_51.0230_16.7700_ci1500"}, out
        )
        out += ["## lookup_business", ""]
        await _call(c, "A company by NIP", "lookup_business", {"identifier": "5250000251"}, out)
        out += ["## find_businesses", ""]
        await _call(
            c, "Plumbers and heating firms in Smolec", "find_businesses", {"town": "Smolec", "pkd": "43.22.Z"}, out
        )
        out += ["## get_job", ""]
        await _call(c, "An unknown job", "get_job", {"job_id": "j_0000000000000000"}, out)
        out += ["## Errors", ""]
        await _call(
            c,
            "Unknown variable, with suggestions",
            "get_timeseries",
            {"area_id": gold["selected"], "variables": ["temperature"], "start": "2024-01-01", "end": "2025-01-01"},
            out,
        )
        await _call(
            c,
            "Area too large",
            "get_timeseries",
            {
                "area_id": "a_pt_51.1100_17.0320_sq20000",
                "variables": ["ndvi"],
                "start": "2024-01-01",
                "end": "2025-01-01",
            },
            out,
        )
    OUT.write_text("\n".join(out).replace(str(Path.home()), "~"))  # no personal paths in the repo
    print(f"wrote {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
