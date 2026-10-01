"""冷链探头读数判定与温漂校准偏置的唯一规则源。

偏置表提交校验、读数提交、履历冻结以及工人判定链路共用本模块的同一套
常量与函数，任何一环绕开这里自行实现都视为缺陷（“少一环整题不过”）。

判定口径：
- 原文温度 + 校准偏置 = 判定用温度；
- 判定用温度不超过 8℃ 为合格，否则为超温；
- 偏置必须落在允许的闭区间 ``[BIAS_MIN_C, BIAS_MAX_C]`` 内（含端点）。
"""

# 判定上限（摄氏度，闭区间端点：恰好 8℃ 也算合格）
TEMP_LIMIT_C = 8.0

# 温漂校准偏置允许的闭区间（摄氏度，含端点）
BIAS_MIN_C = -10.0
BIAS_MAX_C = 10.0

# 偏置越界时网页与直连必须返回的同一句措辞（服务端唯一来源）
BIAS_OUT_OF_RANGE_DETAIL = (
    f"偏置必须在 {BIAS_MIN_C:g}℃ 到 {BIAS_MAX_C:g}℃ 的闭区间内"
)


class BiasOutOfRange(ValueError):
    """偏置越出允许闭区间。"""


def validate_bias(bias_c: float) -> float:
    """校验偏置落在闭区间内，越界抛 :class:`BiasOutOfRange`。"""
    if not (BIAS_MIN_C <= bias_c <= BIAS_MAX_C):
        raise BiasOutOfRange(BIAS_OUT_OF_RANGE_DETAIL)
    return bias_c


def apply_bias(raw_temp_c: float, bias_c: float) -> float:
    """原文温度加偏置得到判定用温度。"""
    return raw_temp_c + bias_c


def judge_temp(temp_c: float) -> tuple[str, str]:
    """按判定用温度给出结论与说明。"""
    if temp_c <= TEMP_LIMIT_C:
        return "合格", f"判定温度 {temp_c:g}℃ 未超过 {TEMP_LIMIT_C:g}℃ 上限"
    return "超温", f"判定温度 {temp_c:g}℃ 超过 {TEMP_LIMIT_C:g}℃ 冷链上限"


def verdict_for_display(verdict: str | None, status: str) -> str:
    if verdict:
        return verdict
    if status == "pending":
        return "待处理"
    if status == "processing":
        return "处理中"
    return "—"
