"""冷链探头读数判定：判定用温度 = 原文温度 + 温漂校准偏置，不超过 8 为合格。

本模块是偏置校验、偏置应用与判定的唯一来源：
偏置设置提交校验（API）、校准履历（API）、判定链路（API 快照 + worker 判定）
全部走这里的同一套函数，少一环都会漏掉。
"""

# 偏置允许闭区间（摄氏度）。偏置本身允许为负。
BIAS_MIN = -10.0
BIAS_MAX = 10.0
TEMP_LIMIT = 8.0

# 越界统一措辞：网页与直连必须一字不差。
BIAS_OUT_OF_RANGE_MSG = (
    f"偏置必须在 {BIAS_MIN:g} 到 {BIAS_MAX:g} 摄氏度闭区间内"
)
BIAS_NOT_NUMBER_MSG = "偏置必须是数字"


class BiasOutOfRange(ValueError):
    """偏置越出允许闭区间。"""


def validate_bias(value) -> float:
    """校验并返回浮点偏置；非数字或越界都抛 ValueError，消息即对外措辞。"""
    try:
        bias = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(BIAS_NOT_NUMBER_MSG) from exc
    if bias != bias or bias in (float("inf"), float("-inf")):
        raise ValueError(BIAS_NOT_NUMBER_MSG)
    if not BIAS_MIN <= bias <= BIAS_MAX:
        raise BiasOutOfRange(BIAS_OUT_OF_RANGE_MSG)
    return bias


def apply_bias(temp_c: float, bias_c: float) -> float:
    """原文温度加偏置得到判定用温度（保留两位小数）。"""
    return round(float(temp_c) + float(bias_c), 2)


def judge_temp(temp_c: float) -> tuple[str, str]:
    if temp_c <= TEMP_LIMIT:
        return "合格", f"判定温度 {temp_c:g}℃ 未超过 {TEMP_LIMIT:g}℃ 上限（含边界合格）"
    return "超温", f"判定温度 {temp_c:g}℃ 超过 {TEMP_LIMIT:g}℃ 冷链上限"


def verdict_for_display(verdict: str | None, status: str) -> str:
    if verdict:
        return verdict
    if status == "pending":
        return "待处理"
    if status == "processing":
        return "处理中"
    return "—"
