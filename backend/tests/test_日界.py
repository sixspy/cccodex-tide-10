"""日界切开关窗三场景：同日成片 / 跨日点两形态 / 切日并发无跨日片。"""
from pathlib import Path
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["数据目录"] = _tmp
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")

import app.db as _db

_db.数据目录 = Path(_tmp)
_db.库路径 = Path(_tmp) / "潮切.db"

import pytest

from app import seed
from app.services import 装载, 关窗, 贯验, 目录, 时间, 夹具

所有表 = (
    "观测点", "入窗缓冲", "窗切片账", "迟到匣", "窗贯验",
    "装载批次", "关窗批次", "日界封账", "跨日归档", "窗规",
)


def _清库():
    conn = _db.连接()
    try:
        for t in 所有表:
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
    finally:
        conn.close()


def _设日窗(形态: str, 窗起: str = "2026-11-24T00:00:00Z"):
    起日 = 时间.解析时(窗起).date()
    止 = (起日 + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    目录.改窗规({
        "当前窗号": 时间.格式窗号("W", 窗起),
        "窗起": 窗起,
        "窗止": 止,
        "窗长秒": "86400",
        "日界形态": 形态,
    })


def _日内片(片) -> bool:
    return 时间.解析时(片["窗起"]).date() == (时间.解析时(片["窗止"]) - timedelta(seconds=1)).date()


@pytest.fixture(autouse=True)
def _db_init():
    seed.初始化库()
    _清库()
    yield


def test_同日成片():
    """同日点不触发切日：成片、无日封、贯验对齐。"""
    _设日窗("切流")
    报告 = 装载.装一批(夹具.读取("同日成片样例"))
    assert 报告["入缓冲"] == 3
    assert 报告["迟到"] == 0
    关 = 关窗.关当前窗()
    assert 关["成片数"] == 2  # 运单-M / 运单-N
    片 = 目录.切片列表()
    assert len(片) == 2
    assert all(_日内片(s) for s in 片)
    assert 贯验.跑一次()["是否对齐"] is True
    状态 = 目录.日界状态()
    assert 状态["封账"] == []  # 同日不钉日封


def test_跨日点两形态():
    """跨日点：切流昨窗不增有日封拒进昨窗；双写昨窗不增归档有。"""
    # —— 形态一：切流 ——
    _设日窗("切流")
    装载.装一批(夹具.读取("同日成片样例"))  # 昨日窗非空
    报告 = 装载.装一批(夹具.读取("跨日点样例"))
    明细 = {d.get("事件编号"): d["命运"] for d in 报告["明细"] if d.get("事件编号")}
    assert 明细["E-cross-1"] == "入缓冲"  # 今日窗另起，跨日点进新窗
    assert 明细["E-cross-2"] == "迟到"    # 切流：跨日点拒进昨窗
    状态 = 目录.日界状态()
    封 = 状态["最近"]
    assert 封["日封键"] == "日封-20261124"
    assert 封["旧窗号"] == "W-20261124-00"
    assert 封["新窗号"] == "W-20261125-00"
    assert 封["valid"] is True
    assert 封["封后增行"] == 0  # 跨日点后昨日窗不再增行
    昨缓冲 = [r for r in 目录.缓冲列表() if r["窗号"] == 封["旧窗号"]]
    assert 昨缓冲 and all(r["命运"] == "已物化" for r in 昨缓冲)  # 昨窗切窗时已成片
    assert any(r["事件编号"] == "E-cross-2" for r in 目录.迟到列表())
    assert any(
        r["事件编号"] == "E-cross-1" and r["窗号"] == 封["新窗号"] and r["命运"] == "在缓冲"
        for r in 目录.缓冲列表()
    )
    assert 贯验.跑一次()["是否对齐"] is True

    # —— 形态二：双写 ——
    _清库()
    _设日窗("双写")
    装载.装一批(夹具.读取("同日成片样例"))
    报告 = 装载.装一批(夹具.读取("跨日点样例"))
    明细 = {d.get("事件编号"): d["命运"] for d in 报告["明细"] if d.get("事件编号")}
    assert 明细["E-cross-1"] == "入缓冲"
    assert 明细["E-cross-2"] == "归档"  # 双写：跨日点进只读归档
    状态 = 目录.日界状态()
    封 = 状态["最近"]
    assert 封["日封键"] == "日封-20261124"
    assert 封["封后增行"] == 0  # 昨窗同样不增
    归档 = [r for r in 状态["归档"] if r["事件编号"] == "E-cross-2"]
    assert len(归档) == 1
    assert 归档[0]["日封键"] == "日封-20261124"
    assert 归档[0]["原窗号"] == "W-20261124-00"
    assert not any(r["事件编号"] == "E-cross-2" for r in 目录.迟到列表())
    assert 贯验.跑一次()["是否对齐"] is True


def test_切日并发无跨日片():
    """两路并发跨日灌：日封不双成功，不得出跨日片。"""
    _设日窗("切流")
    装载.装一批(夹具.读取("同日成片样例"))  # 昨日窗有料

    def 灌(i: int):
        return 装载.装一批({
            "批次编号": f"并发-{i}",
            "事件": [{
                "事件编号": f"E-par-{i}",
                "运单键": f"运单-P{i}",
                "网点键": "网点-东站",
                "事件时": f"2026-11-25T0{i}:00:00Z",
            }],
        })

    with ThreadPoolExecutor(max_workers=2) as ex:
        结果 = list(ex.map(灌, [1, 2]))
    assert all(r["入流"] == 1 for r in 结果)  # 两批都正常入流，无锁异常
    状态 = 目录.日界状态()
    assert len(状态["封账"]) == 1  # 同一日界只切一次，不双成功
    assert 状态["封账"][0]["日封键"] == "日封-20261124"
    assert 状态["封账"][0]["封后增行"] == 0
    片 = 目录.切片列表()
    assert 片 and all(_日内片(s) for s in 片)  # 无跨日片
    新窗 = 状态["封账"][0]["新窗号"]
    新窗在缓冲 = [r for r in 目录.缓冲列表() if r["窗号"] == 新窗 and r["命运"] == "在缓冲"]
    assert len(新窗在缓冲) == 2  # 两个跨日点都进今日新窗
    assert 贯验.跑一次()["是否对齐"] is True
