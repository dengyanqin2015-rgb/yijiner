import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta


class DataCache:
    """DataFrame CSV 文件缓存"""

    def __init__(self, cache_dir: str = "data/cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _make_key(self, func_name: str, *args, **kwargs) -> str:
        parts = [func_name] + [str(a) for a in args]
        parts += [f"{k}={v}" for k, v in sorted(kwargs.items())]
        return "_".join(parts).replace("/", "_").replace("\\", "_")

    def get(self, func_name: str, *args, **kwargs) -> pd.DataFrame | None:
        key = self._make_key(func_name, *args, **kwargs)
        fpath = self.cache_dir / f"{key}.csv"
        if not fpath.exists():
            return None
        mtime = datetime.fromtimestamp(fpath.stat().st_mtime)
        if datetime.now() - mtime > timedelta(hours=24):
            return None
        try:
            df = pd.read_csv(fpath, dtype=str)
            # 将数值类列转回 float（除代码/名称/日期类列外）
            for col in df.columns:
                if col in ("代码", "名称", "日期", "涨停统计", "所属行业", "首次封板时间", "最后封板时间"):
                    continue
                try:
                    df[col] = pd.to_numeric(df[col])
                except (ValueError, TypeError):
                    pass
            return df
        except Exception:
            return None

    def set(self, df: pd.DataFrame, func_name: str, *args, **kwargs):
        key = self._make_key(func_name, *args, **kwargs)
        fpath = self.cache_dir / f"{key}.csv"
        df.to_csv(fpath, index=False, encoding="utf-8-sig")
