from __future__ import annotations


class AnalysisCancelled(Exception):
    """用户关闭前端窗口后，用这个异常中断后续分析步骤。"""

