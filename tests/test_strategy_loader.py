"""Unit tests for agent/strategy_loader.py summary cache fingerprint."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from deeppulse.agent.strategy_loader import StrategyLoader


def _make_loader(tmp_path: Path):
    strat_dir = tmp_path / "strategies"
    strat_dir.mkdir()
    (strat_dir / "01-放量突破.md").write_text("# 放量突破\n内容", encoding="utf-8")
    loader = StrategyLoader.__new__(StrategyLoader)
    loader.dir = strat_dir
    loader.strategies = {"01-放量突破": "内容"}
    return loader


class TestSummaryCacheFingerprint:
    def test_fingerprint_json_round_trip_identity(self):
        """指纹写入 JSON 再回读后必须与重新计算的指纹相等（tuple/list 类型归一）"""
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            loader = _make_loader(tmp_path)
            cache_file = tmp_path / "cache.json"
            with patch("deeppulse.agent.strategy_loader.CACHE_FILE", cache_file):
                fp = loader._files_fingerprint()
                cache_file.write_text(json.dumps({"files": fp}), encoding="utf-8")
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                assert data["files"] == loader._files_fingerprint()
                assert data["files"] != []  # 含真实文件

    def test_save_then_load_hits_cache(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            loader = _make_loader(tmp_path)
            with patch("deeppulse.agent.strategy_loader.CACHE_FILE", tmp_path / "cache.json"):
                loader._save_summary_cache("摘要内容", max_chars=100)
                # save 后立即 load 应命中缓存（此前 tuple/list 比较恒不等导致缓存永不命中）
                assert loader._load_summary_cache(max_chars=100) == "摘要内容"

    def test_touch_file_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            loader = _make_loader(tmp_path)
            with patch("deeppulse.agent.strategy_loader.CACHE_FILE", tmp_path / "cache.json"):
                loader._save_summary_cache("摘要内容", max_chars=100)
                assert loader._load_summary_cache(max_chars=100) == "摘要内容"
                os.utime(loader.dir / "01-放量突破.md", ns=(1, 1))  # 修改 mtime 使指纹变化
                assert loader._load_summary_cache(max_chars=100) == ""
