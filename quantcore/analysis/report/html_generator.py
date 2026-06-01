import json
import os
import shutil
from pathlib import Path
import polars as pl
from jinja2 import Environment, FileSystemLoader
from quantcore.analysis.report.chart_builder import ChartBuilder

TEMPLATE_DIR = Path(__file__).parent / "templates"


class HTMLReportGenerator:

    def __init__(self):
        self.env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
        self.env.filters['tojson'] = lambda obj: json.dumps(obj, ensure_ascii=False)
        self.chart_builder = ChartBuilder()

    def generate(self, analysis_result: dict, output_path: str | None = None) -> str:
        if output_path is None:
            output_path = f"reports/{analysis_result.get('code', 'unknown')}_report.html"

        daily_dicts = analysis_result.get("daily_df", [])
        if daily_dicts:
            daily_df = pl.DataFrame(daily_dicts)
            try:
                kline_data = self.chart_builder.build_kline(daily_df)
            except Exception:
                kline_data = {"dates": [], "kline_data": [], "volumes": [], "ma5": [], "ma20": [], "ma60": []}
        else:
            kline_data = {"dates": [], "kline_data": [], "volumes": [], "ma5": [], "ma20": [], "ma60": []}

        quality_score = analysis_result.get("quality_score", {})
        score_dict = {k: v for k, v in quality_score.items() if isinstance(v, (int, float))}
        radar_data = self.chart_builder.build_radar(score_dict)

        template = self.env.get_template("stock_report.html")
        echarts_asset_path = self._prepare_local_echarts(output_path)
        html = template.render(
            **analysis_result,
            kline_data=kline_data,
            radar_data=radar_data,
            echarts_asset_path=echarts_asset_path,
        )

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        return output_path

    def _prepare_local_echarts(self, output_path: str) -> str | None:
        source_candidates = [
            Path.cwd() / "frontend" / "node_modules" / "echarts" / "dist" / "echarts.min.js",
            Path(__file__).resolve().parents[3] / "frontend" / "node_modules" / "echarts" / "dist" / "echarts.min.js",
        ]
        source = next((path for path in source_candidates if path.exists()), None)
        if source is None:
            return None

        output_dir = Path(output_path).parent
        asset_dir = output_dir / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        target = asset_dir / "echarts.min.js"
        if not target.exists() or target.stat().st_size != source.stat().st_size:
            shutil.copyfile(source, target)
        return os.path.relpath(target, output_dir).replace(os.sep, "/")
