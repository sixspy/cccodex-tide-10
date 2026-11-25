"""观测点装载 → 入窗或迟到。"""
from __future__ import annotations
import json
import uuid
from app.db import 连接
from app.seed import 此刻
from app.services import 校验, 时间
from app.modules import 日界切开关窗 as 日界


def 装一批(体: dict) -> dict:
    批次 = 体.get("批次编号") or f"潮批-{uuid.uuid4().hex[:8]}"
    事件 = 体.get("事件") or []
    报告 = {"批次编号": 批次, "入流": 0, "入缓冲": 0, "迟到": 0, "驳回": 0, "归档": 0, "明细": []}
    for e in 校验.校验批次(体):
        报告["明细"].append({"命运": "批次预检", "说明": e})
    conn = 连接()
    try:
        # 写锁串行：装载/日界切窗/关窗互斥，跨日灌与关窗不会夹出跨日脏片
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        规 = {r["键"]: r["值"] for r in conn.execute("SELECT 键,值 FROM 窗规").fetchall()}
        for ev in 事件:
            行错 = 校验.校验事件(ev)
            if 行错:
                报告["驳回"] += 1
                报告["明细"].append({"命运": "驳回-校验", "说明": ";".join(行错)})
                continue
            编号 = ev["事件编号"]
            if conn.execute("SELECT 1 FROM 观测点 WHERE 事件编号=?", (编号,)).fetchone():
                报告["驳回"] += 1
                报告["明细"].append({"命运": "驳回-幂等", "说明": 编号})
                continue
            处理 = 此刻()
            conn.execute(
                """INSERT INTO 观测点(事件编号,运单键,网点键,事件时,处理时,命运,批次编号)
                   VALUES (?,?,?,?,?,?,?)""",
                (编号, ev["运单键"], ev["网点键"], ev["事件时"], 处理, "已装载", 批次),
            )
            报告["入流"] += 1
            if 日界.启用(规) and 时间.是否跨日界(规.get("窗起", "2026-11-24T00:00:00Z"), ev["事件时"]):
                # 跨日到达：按选定形态切窗封印；切窗/钉封/开窗与本批同票
                处置 = 日界.跨日处置(conn, 规, ev, 处理)
                规 = 处置["新规"]
                for 切 in 处置["切窗"]:
                    报告["明细"].append({
                        "命运": "日界切窗", "日封键": 切["日封键"],
                        "旧窗号": 切["旧窗号"], "新窗号": 切["新窗号"], "成片数": 切["成片数"],
                    })
                去向 = 处置["去向"]
                报告[去向] += 1
                条 = {"命运": 去向, "事件编号": 编号}
                if 去向 == "入缓冲":
                    条["窗号"] = 处置["窗号"]
                if 去向 == "归档":
                    条["日封键"] = 处置["日封键"]
                    条["原窗号"] = 处置["原窗号"]
                报告["明细"].append(条)
                continue
            if 时间.在窗内(ev["事件时"], 规.get("窗起", "2026-11-24T00:00:00Z"), 规.get("窗止", "2026-11-24T01:00:00Z")):
                窗号 = 规.get("当前窗号", "W-demo")
                conn.execute(
                    """INSERT INTO 入窗缓冲(事件编号,运单键,网点键,事件时,窗号,命运,入缓冲时)
                       VALUES (?,?,?,?,?,?,?)""",
                    (编号, ev["运单键"], ev["网点键"], ev["事件时"], 窗号, "在缓冲", 处理),
                )
                conn.execute("UPDATE 观测点 SET 命运=? WHERE 事件编号=?", ("已入缓冲", 编号))
                报告["入缓冲"] += 1
                报告["明细"].append({"命运": "入缓冲", "事件编号": 编号, "窗号": 窗号})
            else:
                conn.execute(
                    """INSERT INTO 迟到匣(事件编号,运单键,原因,事件时,入匣时)
                       VALUES (?,?,?,?,?)""",
                    (编号, ev["运单键"], "事件时出窗", ev["事件时"], 处理),
                )
                conn.execute("UPDATE 观测点 SET 命运=? WHERE 事件编号=?", ("已迟到", 编号))
                报告["迟到"] += 1
                报告["明细"].append({"命运": "迟到", "事件编号": 编号})
        conn.execute(
            """INSERT INTO 装载批次(编号,时刻,入流,入缓冲,迟到,报告) VALUES (?,?,?,?,?,?)""",
            (批次, 此刻(), 报告["入流"], 报告["入缓冲"], 报告["迟到"], json.dumps(报告, ensure_ascii=False)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 报告
