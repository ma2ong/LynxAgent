from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List

import pandas as pd

from .models import DataLakeSyncResult


DEFAULT_A_SHARE_POOL = [
    {"symbol": "603416", "name": "信捷电气", "market": "A股"},
    {"symbol": "002475", "name": "立讯精密", "market": "A股"},
    {"symbol": "688981", "name": "中芯国际", "market": "A股"},
    {"symbol": "600487", "name": "亨通光电", "market": "A股"},
    {"symbol": "002747", "name": "埃斯顿", "market": "A股"},
    {"symbol": "002979", "name": "雷赛智能", "market": "A股"},
    {"symbol": "002407", "name": "多氟多", "market": "A股"},
    {"symbol": "603618", "name": "杭电股份", "market": "A股"},
    {"symbol": "300276", "name": "三丰智能", "market": "A股"},
    {"symbol": "000988", "name": "华工科技", "market": "A股"},
    {"symbol": "603986", "name": "兆易创新", "market": "A股"},
    {"symbol": "002281", "name": "光迅科技", "market": "A股"},
    {"symbol": "300124", "name": "汇川技术", "market": "A股"},
    {"symbol": "688008", "name": "澜起科技", "market": "A股"},
    {"symbol": "688006", "name": "杭可科技", "market": "A股"},
    {"symbol": "920001", "name": "纬达光电", "market": "A股"},
    {"symbol": "688045", "name": "必易微", "market": "A股"},
    {"symbol": "000338", "name": "潍柴动力", "market": "A股"},
    {"symbol": "000099", "name": "中信海直", "market": "A股"},
    {"symbol": "688062", "name": "迈威生物-U", "market": "A股"},
    {"symbol": "300750", "name": "宁德时代", "market": "A股"},
    {"symbol": "600941", "name": "中国移动", "market": "A股"},
    {"symbol": "600584", "name": "长电科技", "market": "A股"},
    {"symbol": "002371", "name": "北方华创", "market": "A股"},
    {"symbol": "300033", "name": "同花顺", "market": "A股"},
    {"symbol": "300024", "name": "机器人", "market": "A股"},
    {"symbol": "601728", "name": "中国电信", "market": "A股"},
    {"symbol": "601138", "name": "工业富联", "market": "A股"},
    {"symbol": "300308", "name": "中际旭创", "market": "A股"},
    {"symbol": "000063", "name": "中兴通讯", "market": "A股"},
    {"symbol": "600276", "name": "恒瑞医药", "market": "A股"},
    {"symbol": "600519", "name": "贵州茅台", "market": "A股"},
    {"symbol": "000001", "name": "平安银行", "market": "A股"},
    {"symbol": "601318", "name": "中国平安", "market": "A股"},
    {"symbol": "600036", "name": "招商银行", "market": "A股"},
]


def _safe_str(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


class AKShareDataLake:
    stock_pool_collection = "quant_stock_pool"

    def fetch_a_share_pool(self, limit: int = 5000) -> List[Dict[str, Any]]:
        try:
            import akshare as ak

            df = ak.stock_info_a_code_name()
            if df is None or df.empty:
                return DEFAULT_A_SHARE_POOL[:limit]

            records: List[Dict[str, Any]] = []
            for _, row in df.head(limit).iterrows():
                symbol = _safe_str(row.get("code") or row.get("证券代码") or row.get("股票代码"))
                name = _safe_str(row.get("name") or row.get("证券简称") or row.get("股票简称"))
                if symbol:
                    records.append({"symbol": symbol.zfill(6), "name": name, "market": "A股"})
            return records or DEFAULT_A_SHARE_POOL[:limit]
        except Exception:
            return DEFAULT_A_SHARE_POOL[:limit]

    async def sync_a_share_pool(self, db=None, limit: int = 5000) -> DataLakeSyncResult:
        pool = self.fetch_a_share_pool(limit)
        if db is None:
            return DataLakeSyncResult(
                source="akshare",
                collection=self.stock_pool_collection,
                total=len(pool),
                inserted=0,
                updated=0,
                errors=["MongoDB is not available; returned live AKShare pool only"],
            )

        collection = db[self.stock_pool_collection]
        inserted = 0
        updated = 0
        now = datetime.utcnow()
        for item in pool:
            doc = {**item, "source": "akshare", "updated_at": now}
            result = await collection.update_one(
                {"symbol": item["symbol"]},
                {"$set": doc, "$setOnInsert": {"created_at": now}},
                upsert=True,
            )
            inserted += 1 if result.upserted_id else 0
            updated += 1 if result.matched_count else 0

        await collection.create_index("symbol", unique=True)
        await collection.create_index([("market", 1), ("updated_at", -1)])
        return DataLakeSyncResult(
            source="akshare",
            collection=self.stock_pool_collection,
            total=len(pool),
            inserted=inserted,
            updated=updated,
        )

    async def get_stock_pool(self, db=None, limit: int = 200) -> Dict[str, Any]:
        if db is not None:
            docs = await db[self.stock_pool_collection].find({}, {"_id": 0}).sort("symbol", 1).limit(limit).to_list(length=limit)
            if docs:
                return {"source": "mongodb", "total": len(docs), "items": docs}

        items = self.fetch_a_share_pool(limit)
        return {"source": "akshare", "total": len(items), "items": items}


def dataclass_to_dict(result: DataLakeSyncResult) -> Dict[str, Any]:
    return asdict(result)
