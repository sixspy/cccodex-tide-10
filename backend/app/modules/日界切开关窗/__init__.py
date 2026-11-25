"""日界切开关窗：跨日到达按选定形态切窗封印（按日关窗编排管道）。

形态（窗规键 `日界形态` 选定）：
- 切流：切流账册日界强制封。昨日空或非空均钉日封，今日窗另起，跨日点拒进昨窗。
- 双写：双写两轨。跨日点进只读归档（跨日归档），现行只读当日窗；读引用不同。

硬约束：
- 切窗封昨日与开今日同成败：物化旧窗切片、钉日封、改窗规写在调用方同一事务里。
- 跨日灌与关窗并发靠 BEGIN IMMEDIATE 写锁串行；日封键主键兜底，不会双成功。
- 禁止只改日期展示：当前窗号/窗起/窗止真实切换，切片按新窗物化。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from app.services import 关窗
from app.services.时间 import 解析时, 格式窗号

NAME = "日界切开关窗"
CONTRACT = "跨日界时强制切窗号并封上一窗"

形态_切流 = "切流"
形态_双写 = "双写"
形态集 = (形态_切流, 形态_双写)

_FMT = "%Y-%m-%dT%H:%M:%SZ"


def describe() -> dict:
    return {
        "模块": NAME,
        "契约": CONTRACT,
        "形态": list(形态集),
        "默认启用": True,
        "说明": "窗规 `日界形态` 选定形态；缺省/未知值视为未启用，走基线",
    }


def 启用(规: dict) -> bool:
    return (规 or {}).get("日界形态") in 形态集


def 形态(规: dict) -> str:
    return (规 or {}).get("日界形态") or 形态_切流


def 日界键(窗起: str) -> str:
    return f"日封-{解析时(窗起).strftime('%Y%m%d')}"


def _日界(日) -> datetime:
    return datetime(日.year, 日.month, 日.day, tzinfo=timezone.utc)


def 切日界(conn, 规: dict, 时刻: str) -> dict:
    """把当前窗切到下一日界：物化旧窗（空也钉封）+ 插日封 + 改窗规，同一事务。

    调用方须已持有写事务（BEGIN IMMEDIATE）；本函数不 commit/rollback，
    与调用方同成败。日封键主键冲突即失败回滚，保证不双成功。
    """
    旧窗号 = 规.get("当前窗号", "W-demo")
    旧起 = 规.get("窗起", "")
    旧止 = 规.get("窗止", "")
    旧日 = 解析时(旧起).date()
    新起 = (_日界(旧日) + timedelta(days=1)).strftime(_FMT)
    新止 = (_日界(旧日) + timedelta(days=2)).strftime(_FMT)
    新窗号 = 格式窗号("W", 新起)
    日封键 = 日界键(旧起)
    # 昨日空或非空均钉日封：先物化旧窗（空窗成片数为 0，也照钉）
    物化 = 关窗.物化当前窗(conn, 旧窗号, 旧起, 旧止, 时刻)
    批 = f"切日-{uuid.uuid4().hex[:8]}"
    关窗报告 = {
        "批次编号": 批, "窗号": 旧窗号, "成片数": 物化["成片数"],
        "缓冲消耗": 物化["缓冲消耗"], "日封键": 日封键,
    }
    conn.execute(
        "INSERT INTO 关窗批次(编号,窗号,时刻,成片数,报告) VALUES (?,?,?,?,?)",
        (批, 旧窗号, 时刻, 物化["成片数"], json.dumps(关窗报告, ensure_ascii=False)),
    )
    conn.execute(
        """INSERT INTO 日界封账(日封键,旧窗号,新窗号,旧窗起,旧窗止,新窗起,新窗止,形态,封时,有效)
           VALUES (?,?,?,?,?,?,?,?,?,1)""",
        (日封键, 旧窗号, 新窗号, 旧起, 旧止, 新起, 新止, 形态(规), 时刻),
    )
    # 窗规与切片同票：同一事务切到今日窗
    for k, v in {"当前窗号": 新窗号, "窗起": 新起, "窗止": 新止, "窗长秒": "86400"}.items():
        conn.execute(
            "INSERT INTO 窗规(键,值) VALUES (?,?) ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
            (k, v),
        )
    新规 = {**规, "当前窗号": 新窗号, "窗起": 新起, "窗止": 新止, "窗长秒": "86400"}
    return {
        "成功": True, "日封键": 日封键, "旧窗号": 旧窗号, "新窗号": 新窗号,
        "成片数": 物化["成片数"], "新规": 新规,
    }


def 跨日处置(conn, 规: dict, ev: dict, 时刻: str) -> dict:
    """处置跨日点：未来日先逐日切窗（空日也钉封）再入新窗；过去日按形态拒进昨窗/归档。"""
    事件日 = 解析时(ev["事件时"]).date()
    切窗 = []
    while 启用(规) and 解析时(规.get("窗起", "")).date() < 事件日:
        切 = 切日界(conn, 规, 时刻)
        切窗.append(切)
        规 = 切["新规"]
    窗日 = 解析时(规.get("窗起", "")).date()
    编号 = ev["事件编号"]
    if 事件日 == 窗日:
        # 切到当日，今日窗另起：跨日点进今日新窗，不进昨窗
        conn.execute(
            """INSERT INTO 入窗缓冲(事件编号,运单键,网点键,事件时,窗号,命运,入缓冲时)
               VALUES (?,?,?,?,?,?,?)""",
            (编号, ev["运单键"], ev["网点键"], ev["事件时"], 规["当前窗号"], "在缓冲", 时刻),
        )
        conn.execute("UPDATE 观测点 SET 命运=? WHERE 事件编号=?", ("已入缓冲", 编号))
        return {"去向": "入缓冲", "窗号": 规["当前窗号"], "新规": 规, "切窗": 切窗}
    # 事件日 < 窗日：昨日点，昨窗已封
    if 形态(规) == 形态_双写:
        封 = conn.execute(
            "SELECT 日封键,旧窗号 FROM 日界封账 ORDER BY 封时 DESC, rowid DESC LIMIT 1"
        ).fetchone()
        原窗号 = 封["旧窗号"] if 封 else 格式窗号("W", ev["事件时"])
        日封键 = 封["日封键"] if 封 else ""
        conn.execute(
            """INSERT INTO 跨日归档(事件编号,运单键,网点键,事件时,原窗号,日封键,归档时)
               VALUES (?,?,?,?,?,?,?)""",
            (编号, ev["运单键"], ev["网点键"], ev["事件时"], 原窗号, 日封键, 时刻),
        )
        conn.execute("UPDATE 观测点 SET 命运=? WHERE 事件编号=?", ("已归档", 编号))
        return {"去向": "归档", "原窗号": 原窗号, "日封键": 日封键, "新规": 规, "切窗": 切窗}
    conn.execute(
        "INSERT INTO 迟到匣(事件编号,运单键,原因,事件时,入匣时) VALUES (?,?,?,?,?)",
        (编号, ev["运单键"], "日界已封拒进昨窗", ev["事件时"], 时刻),
    )
    conn.execute("UPDATE 观测点 SET 命运=? WHERE 事件编号=?", ("已迟到", 编号))
    return {"去向": "迟到", "新规": 规, "切窗": 切窗}


def extra_verify_checks(conn) -> list[str]:
    """贯验附加检查：跨日片 / 日封后昨窗增行 / 归档引用的日封须存在。"""
    错: list[str] = []
    for r in conn.execute("SELECT 切片编号,窗起,窗止 FROM 窗切片账").fetchall():
        if 解析时(r["窗起"]).date() != (解析时(r["窗止"]) - timedelta(seconds=1)).date():
            错.append(f"跨日片:{r['切片编号']}")
    for 封 in conn.execute("SELECT 日封键,旧窗号,封时 FROM 日界封账").fetchall():
        c = conn.execute(
            "SELECT COUNT(*) AS c FROM 入窗缓冲 WHERE 窗号=? AND 入缓冲时>?",
            (封["旧窗号"], 封["封时"]),
        ).fetchone()["c"]
        if c:
            错.append(f"日封后昨窗增行:{封['旧窗号']}x{c}")
    for r in conn.execute("SELECT 事件编号,日封键 FROM 跨日归档 WHERE 日封键<>''").fetchall():
        if not conn.execute("SELECT 1 FROM 日界封账 WHERE 日封键=?", (r["日封键"],)).fetchone():
            错.append(f"归档无日封:{r['事件编号']}")
    return 错
