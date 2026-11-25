"""列表与窗规读写。"""
from __future__ import annotations
from app.db import 连接


def 窗规() -> dict:
    conn = 连接()
    try:
        rows = conn.execute("SELECT 键,值 FROM 窗规").fetchall()
        return {r["键"]: r["值"] for r in rows}
    finally:
        conn.close()


def 改窗规(体: dict) -> dict:
    体 = 体 or {}
    if "日界形态" in 体 and 体["日界形态"] not in ("切流", "双写"):
        return {"错误": "日界形态须为 切流/双写"}
    conn = 连接()
    try:
        for k, v in 体.items():
            conn.execute(
                "INSERT INTO 窗规(键,值) VALUES (?,?) ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
                (str(k), str(v)),
            )
        conn.commit()
        return 窗规()
    finally:
        conn.close()


def 日界状态() -> dict:
    """日界只读视图：旧窗号、新窗号、日封键、valid；附封账与只读归档。"""
    conn = 连接()
    try:
        规 = {r["键"]: r["值"] for r in conn.execute("SELECT 键,值 FROM 窗规").fetchall()}
        封账 = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM 日界封账 ORDER BY 封时 DESC, rowid DESC"
            ).fetchall()
        ]
        for 封 in 封账:
            增行 = conn.execute(
                "SELECT COUNT(*) AS c FROM 入窗缓冲 WHERE 窗号=? AND 入缓冲时>?",
                (封["旧窗号"], 封["封时"]),
            ).fetchone()["c"]
            封["封后增行"] = 增行
            封["valid"] = bool(封["有效"]) and 增行 == 0
        归档 = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM 跨日归档 ORDER BY 编号 DESC LIMIT 200"
            ).fetchall()
        ]
        return {
            "启用": 规.get("日界形态") in ("切流", "双写"),
            "形态": 规.get("日界形态", ""),
            "当前窗号": 规.get("当前窗号", ""),
            "最近": 封账[0] if 封账 else None,
            "封账": 封账,
            "归档": 归档,
        }
    finally:
        conn.close()


def 网点列表() -> list[dict]:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 网点源 ORDER BY 源键").fetchall()]
    finally:
        conn.close()


def 观测列表() -> list[dict]:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 观测点 ORDER BY 事件时 DESC LIMIT 200").fetchall()]
    finally:
        conn.close()


def 缓冲列表() -> list[dict]:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 入窗缓冲 ORDER BY 事件时").fetchall()]
    finally:
        conn.close()


def 切片列表() -> list[dict]:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 窗切片账 ORDER BY 物化时 DESC LIMIT 200").fetchall()]
    finally:
        conn.close()


def 迟到列表() -> list[dict]:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 迟到匣 ORDER BY 编号 DESC LIMIT 200").fetchall()]
    finally:
        conn.close()


def 贯验列表() -> list[dict]:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 窗贯验 ORDER BY 编号 DESC LIMIT 50").fetchall()]
    finally:
        conn.close()


def 概览() -> dict:
    conn = 连接()
    try:
        return {
            "观测点": conn.execute("SELECT COUNT(*) AS c FROM 观测点").fetchone()["c"],
            "在缓冲": conn.execute("SELECT COUNT(*) AS c FROM 入窗缓冲 WHERE 命运='在缓冲'").fetchone()["c"],
            "窗切片": conn.execute("SELECT COUNT(*) AS c FROM 窗切片账").fetchone()["c"],
            "迟到": conn.execute("SELECT COUNT(*) AS c FROM 迟到匣").fetchone()["c"],
            "窗规": 窗规(),
        }
    finally:
        conn.close()
