#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
opc-bootstrap-validator · OPC 启动验证器 · 判据引擎
---------------------------------------------------
将《OPC 单人创业自查与最小验证流程》的表格判据固化为确定性计算。
标准库零依赖。用法：python3 gate.py <command> [args]

设计原则（不可违反）：
  1. 判据先于评分：硬红线命中即锁定，任何评分不得覆盖。
  2. 无证据封顶 2 分：无事实记录的打分一律按 2 分计入，由代码执行，不靠自觉。
  3. 不做鼓励性表达：最高档结论是「可进入第二层自查」，不是「可以全职」。
  4. 数据不全即拒答：有收入无工时等情形输出「无法判定」，不给数字。
"""

import argparse
import datetime
import html as _html
import json
import math
import os
import re
import sys

VERSION = "0.6.0"
ENGINE = "opc-bootstrap-validator"
SCHEMA = "opc-bootstrap-validator/1"

# ══════════════════════════════════════════════════════════════════
# 判据常量（出处见 references/方法论全文.md，章节号对应 SKILL.md 对照表）
# ══════════════════════════════════════════════════════════════════

# 硬红线 · 第二章 2.1
HARD_REDLINES = [
    ("no_cushion_no_income", "没有经过核算的生活费安全垫，且短期内没有稳定现金流来源"),
    ("license_unobtainable", "业务需要强制资质、许可、备案或执业资格，当前无法合法取得或借用合规主体承接"),
    ("uncontrolled_risk", "业务可能触人身安全、重大财产损失、敏感信息、未成年人信息、版权或商业秘密，尚未建立风控方案"),
    ("health_family_imbalance", "个人健康、心理状态、照护责任或家庭关系已处于明显失衡状态"),
    ("full_time_breaks_life", "全职尝试会导致高息负债、基本生活中断、家庭义务无法履行，或无法承担最坏情形"),
    ("illegal_acquisition", "计划依赖违法获客、虚假宣传、侵权素材、未经授权的数据或规避平台规则"),
]

# 软风险项 · 第二章 2.2
SOFT_RISKS = [
    ("no_full_cycle", "从未独立完成「获客—报价—签约—交付—收款—售后」的完整闭环"),
    ("sales_aversion", "极度排斥销售、沟通、谈判或被拒绝"),
    ("escape_motive", "主要动机只是逃离职场，尚未确认愿意长期承担客户结果"),
    ("executor_only", "只愿意执行任务，不愿意进行客户选择、定价、取舍和业务判断"),
    ("imaginary_customers", "对目标客户缺乏真实接触，主要依靠想象判断需求"),
    ("single_dependency", "业务方向依赖单一平台、单一客户或单一工具"),
    ("no_compliance_awareness", "对合同、发票、税务、知识产权、数据安全和售后边界没有基本认识"),
]

# 适配度评分项 · 第三章 3.2（权重合计 100）
SCORE_ITEMS = [
    ("motive_choice", "动机与耐久度", "主动选择经营业务，而非仅为逃避职场", 5),
    ("tolerate_uncertainty", "动机与耐久度", "接受不确定性，并能在没有外部监督时推进", 5),
    ("long_term_roles", "动机与耐久度", "能长期承担销售、交付、售后和复盘", 5),
    ("rejection_resilience", "动机与耐久度", "在连续被拒绝时仍能调整而非停摆", 5),
    ("full_cycle_paid", "能力闭环", "曾独立完成项目并成功收款", 10),
    ("sellable_skill", "能力闭环", "具备可对外售卖的专业能力或可验证成果", 8),
    ("boundary_judgment", "能力闭环", "能判断业务边界、风险和不适合服务的客户", 6),
    ("sales_learning", "能力闭环", "能学习并执行获客、报价和谈判", 6),
    ("cash_cushion", "资源与风险", "有可核算的现金安全垫或稳定现金流", 8),
    ("time_capacity", "资源与风险", "每周可稳定投入时间，家庭责任已安排", 6),
    ("compliance_basics", "资源与风险", "了解基本合同、财税、知识产权和数据安全要求", 6),
    ("paid_evidence", "市场证据", "已有付费客户、定金或可核实的购买行为", 10),
    ("customer_clarity", "市场证据", "能清晰描述目标客户和高频痛点", 5),
    ("reachable_channel", "市场证据", "已有低成本、可重复触达客户的渠道", 5),
    ("standardizable", "OPC 结构", "交付可以标准化，AI 能提高效率而非替代责任", 5),
    ("light_structure", "OPC 结构", "不依赖大团队、重资产或长期垫资", 5),
]

SCORE_LEVELS = [
    (0, "没有能力、资源或行为证据"),
    (1, "有兴趣或零散经验，但不能稳定复现"),
    (3, "曾经完成过，或正在按计划练习"),
    (4, "能独立稳定完成，并有案例、回款或可验证记录"),
    (5, "已形成可复用方法，能够持续产生结果或教会他人"),
]

NO_EVIDENCE_CAP = 2          # 第三章 3.1：无事实记录最高 2 分
SOFT_RISK_OVERLAY = 3        # 第三章 3.3：软风险命中 ≥3，高分也降级
BAND_READY = 80              # 第三章 3.3
BAND_CONDITIONAL = 60

# 伪验证信号 · 第十二章 12.5
PSEUDO_SIGNALS = [
    ("launched", "产品上线", "上线不代表有人需要",
     "核心功能使用、留存和付费",
     ["上线", "已发布", "正式发布", "正式推出", "发布了", "公测"]),
    ("followers", "粉丝增长", "粉丝可能不是目标客户",
     "目标受众占比、商业转化和复购",
     ["粉丝", "涨粉", "关注量", "关注者", "粉丝数"]),
    ("reads", "文章有阅读量", "阅读不代表愿意付费或持续追读",
     "追读、订阅、稿费和版权收入",
     ["阅读量", "播放量", "浏览量", "阅读数", "流量不错"]),
    ("dtc_orders", "独立站有订单", "订单可能被广告、退款和拒付吞噬",
     "签收后的贡献利润和复购",
     ["有订单", "出单", "订单量", "下了单", "成单"]),
    ("dropship_gmv", "无货源电商有销售额", "销售额没有扣除物流、售后和平台风险",
     "售后后利润、妥投率和退款/拒付率",
     ["销售额", "GMV", "流水", "卖了", "营业额"]),
]

# 方向生成证据来源 · 第四章 4.1
DIRECTION_BASES = [
    ("past_results", "过去 3–5 年反复做过、做出结果且不完全依赖单一雇主的事"),
    ("referrals", "他人反复向你咨询、请你代办或愿意为你节省时间的事"),
    ("market_gap", "你接触过的行业中，客户已在花钱解决但现有方案不理想的问题"),
    ("fast_deliverable", "你能在 14 天内完成最小交付、且能明确验收标准的服务"),
]

# 方向评分卡 · 第四章 4.2（权重合计 100%）
DIRECTION_ITEMS = [
    ("pain_intensity", "痛点和损失强度", 20, "不解决会造成收入损失、成本增加、合规风险或大量时间浪费吗？"),
    ("paid_proof", "已有付费证据", 20, "客户是否已经为相似方案付费，或愿意支付定金？"),
    ("reachability", "客户可触达性", 15, "你能否低成本找到并持续触达这类客户？"),
    ("measurability", "结果可衡量性", 10, "交付后能否用时间、成本、收入、转化率或风险指标验收？"),
    ("delivery_control", "交付可控性", 10, "单人能否在约定时间内完成，且不依赖不可控第三方？"),
    ("unit_econ", "单位经济", 10, "扣除直接成本和交付工时后，是否仍有合理毛利？"),
    ("repeat_referral", "复购与转介绍", 5, "需求是否会重复出现，或客户是否容易介绍同类客户？"),
    ("compliance_dependency", "合规与依赖风险", 5, "是否存在资质、数据、平台、版权或单一客户风险？"),
    ("founder_durability", "个人耐久度", 5, "你是否愿意持续做至少 2–3 年？"),
]

# 第四章 4.2：三项不得低于 3 分，否则该方向不合格
DIRECTION_HARD_FLOOR = ("compliance_dependency", "reachability", "delivery_control")
DIRECTION_FLOOR = 3

# 预验证门槛 · 第五章 5.2
PREVALIDATION = {"interviews_min": 10, "interviews_ideal": 15, "advanced_min": 3, "payers_min": 1}

# 漏斗八环节 · 第五章 5.3
FUNNEL_STAGES = [
    ("targets", "目标名单", "合格潜客数量", "判断市场是否可触达"),
    ("reached", "有效触达", "回复人数和回复率", "判断信息和渠道是否有效"),
    ("interviews", "访谈", "完成次数和完成率", "判断痛点是否真实"),
    ("proposals", "提案", "提案数和接受率", "判断 Offer 是否清楚"),
    ("quotes", "报价", "报价金额和异议", "判断价格与价值匹配"),
    ("payments", "付款", "定金、全款和回款周期", "判断真实付费意愿"),
    ("deliveries", "交付", "按时完成率、返工率", "判断交付可控性"),
    ("results", "结果", "客户结果、复购和转介绍", "判断长期价值"),
]

# 收款失败的归因分类 · 第五章 5.2（不要直接得出"市场不存在"）
LOSS_REASONS = ["需求不足", "信任不足", "价格不足", "时机不对", "决策链不清", "交付不明确"]

# 一页纸启动书 19 字段 · 第六章 6.1
MSO_FIELDS = [
    ("target_customer", "目标客户"), ("scenario", "客户所在场景"),
    ("pain", "最痛且可验证的问题"), ("loss", "不解决该问题的损失"),
    ("min_result", "我提供的最小结果"), ("deliverables", "具体交付物"),
    ("exclusions", "不包含的内容"), ("client_inputs", "客户需要提供的资料"),
    ("cycle_days", "交付周期"), ("acceptance", "验收标准"),
    ("price_terms", "价格、付款节点和退款边界"), ("why_me", "为什么由我来做"),
    ("channel_actions", "获客渠道和每周动作"), ("direct_cost", "预计直接成本"),
    ("delivery_hours", "预计交付工时"), ("gross_profit", "预计单客毛利"),
    ("risks", "合规、数据和知识产权风险"),
    ("stop_loss", "最大投入、最长试跑时间、继续指标"), ("goal_90d", "90 天目标"),
]

# MSO 不可控结果承诺的绝对化表述 · 第六章 6.2 第 6 条
ABSOLUTE_CLAIMS = ["保证", "确保", "一定能", "必然", "100%", "零风险", "包过", "包成功", "稳赚"]

# 书面约定项 · 第六章 6.2 第 7 条
CONTRACT_TERMS = ["定金", "变更", "延期", "退款", "保密", "成果归属", "售后"]

# 立即停止全职投入 · 第十章 10.1
STOP_IMMEDIATE = [
    ("illegal_or_uncontrollable", "业务无法合法承接，或风险已经超出个人可控制范围"),
    ("health_family_decline", "健康、家庭或基本生活出现明显恶化"),
    ("debt_or_life_erosion", "继续投入需要借高息债、挪用生活费或承担无法承受的损失"),
]

# 建议停止或转向（命中任意两项）· 第十章 10.2
STOP_SUGGEST = [
    ("no_paying_after_repair", "完成既定修复期仍没有真实付费客户"),
    ("two_rounds_no_payment", "连续两轮有记录的验证仍无法收款"),
    ("paid_but_losing", "付款客户的交付长期亏损、超时或引发严重返工"),
    ("imagined_feedback", "主要获得的是想象中的市场反馈，没有决策人和付款证据"),
    ("burnout_no_growth", "每次业务行动都造成持续的身心损耗，且没有能力或认知增长"),
    ("escape_only", "只喜欢不上班，不愿意销售、交付和承担客户责任"),
]

# 失败时的调整顺序 · 第七章 7.3
ADJUST_ORDER = [
    "先判断是否找对了客户，是否真的触达决策人",
    "再判断痛点是否足够急，客户当前替代方案是什么",
    "再调整交付范围和结果表达，减少一次性承诺",
    "再测试价格、付款节点和试点形式",
    "最后才考虑彻底更换方向",
]
MAX_ROUNDS = 2
ROUND_DAYS = 30
MAX_VARS_PER_ROUND = 2

# 替代路径 · 第十章 10.3
ALT_PATHS = [
    ("freelance", "自由职业", "有单项技能，愿意按项目售卖时间，但暂不经营完整业务"),
    ("partnership", "合伙创业", "专业能力强，但销售、运营或资金存在明显互补需求"),
    ("early_team", "加入早期团队", "想体验创业，但暂时不想独自承担全部现金流风险"),
    ("intrapreneur", "内部创业", "已有平台、客户、品牌或合规资源可以复用"),
    ("side_project", "副业测试", "方向有兴趣，但需要先保护主业和家庭现金流"),
    ("return_job", "回到专业岗位", "先积累案例、行业资源和资金，再择机重启"),
]

# 信号分级 · 第一章末段 / 第五章 5.2
SIGNAL_LEVELS = [
    (0, "口头兴趣", "有人说感兴趣", "有人真的说想要吗", False, ["感兴趣", "挺好的", "不错", "有需求", "有机会"]),
    (1, "弱信号", "愿意留下联系方式", "留下联系方式了吗", False, ["留了微信", "加了微信", "留了联系方式", "留了电话"]),
    (2, "中等信号", "愿意预约并提供业务信息", "预约并给了业务信息吗", False, ["预约", "给了资料", "提供了资料", "愿意看方案", "约了"]),
    (3, "强验证", "支付定金或全款", "有付款记录吗", True, ["付了定金", "付了全款", "已付款", "回款", "打款", "支付了"]),
]

# 短板修复对照 · 第三章 3.4
REPAIR_MAP = {
    "full_cycle_paid": ("缺少闭环经验", "承接 1 个范围明确的小单，完整完成签约、交付、收款和售后", "1–2 个月",
                        "合同、回款记录、交付物和客户反馈"),
    "sellable_skill": ("不清楚能卖什么", "选择一个细分人群，完成作品集和 3 个案例拆解", "1–3 个月",
                       "一页 Offer、作品集、案例说明"),
    "sales_learning": ("销售恐惧", "完成 20 次有效触达和 10 次结构化访谈", "30 天",
                       "联系记录、访谈纪要、异议清单"),
    "cash_cushion": ("现金储备不足", "降低固定支出，维持主业或增加稳定收入", "3–12 个月",
                     "安全垫月数达到个人目标"),
    "standardizable": ("交付不可重复", "记录每一步交付工时、输入、输出、验收标准", "4–8 周",
                       "SOP、模板、质量检查表"),
    "compliance_basics": ("合规知识不足", "完成所在地的主体、合同、税务、资质、数据和版权初查", "2–4 周",
                          "风险清单和处理方案"),
    "customer_clarity": ("客户画像不清", "锁定一个细分人群，写出 5 个具体客户或账号", "2–4 周",
                         "客户名单、场景描述、痛点原话"),
    "reachable_channel": ("缺少可重复触达渠道", "选定 1–2 个渠道并完成 20 次触达记录", "30 天",
                          "触达记录、回复率、渠道成本"),
    "boundary_judgment": ("业务边界模糊", "写出不服务的客户类型与不承诺的结果清单", "2–4 周",
                          "不服务清单、责任边界说明"),
    "time_capacity": ("时间投入无保障", "固定每周投入时段并与家庭责任排期", "2 周",
                      "周排期表、实际投入记录"),
    "paid_evidence": ("缺少真实付款证据", "取得至少 1 笔定金或全款，可先用极小范围的付费试点验证", "30 天",
                      "付款记录、定金凭证或订单确认"),
    "motive_choice": ("动机待确认", "写清为什么是这件事、以及愿意长期承担的客户结果", "2–4 周",
                      "一页动机与承诺说明"),
    "tolerate_uncertainty": ("自主推进能力不足", "在没有外部监督的情况下连续 8 周完成既定动作并留痕", "8 周",
                             "周推进日志与完成率"),
    "long_term_roles": ("角色承担不足", "完整跑一轮交付与售后，记录最消耗的环节", "1–2 个月",
                        "全流程记录与复盘"),
    "rejection_resilience": ("被拒后易停摆", "记录 20 次被拒，每次写下调整动作", "30 天",
                             "拒绝日志与调整记录"),
    "light_structure": ("结构过重", "拆出单人可完成的最小范围，去除垫资与重资产依赖", "2–4 周",
                        "最小交付清单与成本表"),
}

ASSAY_LEVELS = {
    "LOCKED": "硬红线命中——只做低成本副业测试，不进入全职或扩大投入讨论",
    "INCOMPLETE": "前置条件未完成——止损数字或评分证据缺失，无法判定",
    "INSUFFICIENT": "当前不适配——优先获得真实案例、补充现金储备或训练销售",
    "CONDITIONAL": "有条件适配——制定短板修复计划，修复期内不全职启动",
    "READY_FOR_LAYER2": "第一层自查可放行——进入第二层模式专属指标自查",
}

ASSAY_RANK = {"LOCKED": 0, "INCOMPLETE": 1, "INSUFFICIENT": 2, "CONDITIONAL": 3, "READY_FOR_LAYER2": 4}

EVIDENCE_RULE = ("「有人说感兴趣」是信号；留联系方式是弱信号；预约并给资料是中等信号；"
                 "支付定金或全款才是强验证。没有付款不称已验证（第一章 / 第五章 5.2）。")

DISCLAIMER_INTERNAL = (
    "自用版：判据引擎按既定方法论生成，仅供决策参考。"
    "涉及资质、合同、税务、数据与知识产权的部分，仍需找对应专业的人核验。"
)

DISCLAIMER = (
    "本报告由自动化判据引擎按既定方法论生成，仅供决策参考，不构成商业、法律、税务、"
    "财务或行业专业建议，也不对任何收益或结果作出承诺。"
    "涉及资质、合同、税务、数据、知识产权及受监管行业的判断，须由具备资质的专业人士核验。"
    "报告中「通过」仅表示按本方法论收集到的最低证据已齐备，不代表商业成功。"
)


# ══════════════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════════════

def die(msg, code=2):
    sys.stderr.write("[%s] 错误：%s\n" % (ENGINE, msg))
    sys.exit(code)


def load_config(path):
    if not path:
        return {}
    if not os.path.exists(path):
        die("配置文件不存在：%s" % path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as exc:
        die("配置文件不是合法 JSON：%s（%s）" % (path, exc))


def dump(obj, out=None):
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        return out
    sys.stdout.write(text + "\n")
    return None


def num(v, default=None):
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def blank(v):
    return v is None or (isinstance(v, str) and not v.strip()) or \
           (isinstance(v, (list, dict)) and len(v) == 0)


def section(cfg, key, *parents):
    """读取一个数据段，兼容顶层与嵌套两种写法。

    闸门 3 与闸门 4 各自包含两个子段（方向 + 漏斗 / MSO + 交付），
    用户可能写成顶层 `funnel`，也可能按章节语义写成 `gate3.funnel`。
    两种写法都接受，顶层优先；都不存在时返回空 dict。
    """
    v = cfg.get(key)
    if isinstance(v, dict) and v:
        return v
    for p in parents:
        sub = (cfg.get(p) or {}).get(key)
        if isinstance(sub, dict) and sub:
            return sub
    return v if isinstance(v, dict) else {}


def today():
    return datetime.date.today().isoformat()


def esc(v):
    return _html.escape("" if v is None else str(v))


# ══════════════════════════════════════════════════════════════════
# 闸门 1 · 硬红线 + 软风险 + 止损三数字（第二章）
# ══════════════════════════════════════════════════════════════════

def judge_gate1(cfg):
    g = cfg.get("gate1") or {}
    red = g.get("redlines") or {}
    soft = g.get("soft_risks") or []
    stop = g.get("stop_loss") or {}
    cushion = g.get("safety_cushion") or {}

    hits = []
    for key, text in HARD_REDLINES:
        if red.get(key) is True:
            hits.append({"key": key, "text": text})

    soft_hits = []
    for key in soft:
        label = dict((k, t) for k, t in SOFT_RISKS).get(key)
        soft_hits.append({"key": key, "text": label or key})

    cash = num(cushion.get("cash"))
    monthly = num(cushion.get("monthly_essential"))
    months = round(cash / monthly, 1) if (cash is not None and monthly) else None
    target = num(cushion.get("target_months"))
    cushion_ok = None
    if months is not None and target:
        cushion_ok = months >= target

    missing_stop = [k for k in ("max_cash", "max_weeks", "min_result") if blank(stop.get(k))]

    if hits:
        level, reason = "LOCKED", "命中 %d 条硬红线" % len(hits)
    elif missing_stop:
        level, reason = "INCOMPLETE", "止损三数字未写全：%s" % "、".join(missing_stop)
    else:
        level, reason = ("SOFT_ALERT" if len(soft_hits) >= SOFT_RISK_OVERLAY else "CLEAR"), "未命中硬红线"

    return {
        "level": level,
        "reason": reason,
        "redline_hits": hits,
        "redline_count": len(hits),
        "soft_hits": soft_hits,
        "soft_count": len(soft_hits),
        "soft_overlay_triggered": len(soft_hits) >= SOFT_RISK_OVERLAY,
        "cushion_months": months,
        "cushion_target_months": target,
        "cushion_ok": cushion_ok,
        "stop_loss": stop,
        "stop_loss_missing": missing_stop,
        "note": "硬红线是闸门不是分数：命中即锁定，后续评分不得覆盖（第三章 3.3）。",
    }


# ══════════════════════════════════════════════════════════════════
# 闸门 2 · 适配度评分（第三章）
# ══════════════════════════════════════════════════════════════════

def judge_score(cfg):
    raw = (cfg.get("score") or {}).get("items") or {}
    rows, capped, missing = [], [], []
    dims = {}

    for key, dim, text, weight in SCORE_ITEMS:
        entry = raw.get(key) or {}
        if isinstance(entry, (int, float)):
            entry = {"score": entry}
        s = num(entry.get("score"))
        evidence = (entry.get("evidence") or "").strip()

        flags = []
        if s is None:
            s_eff, note = NO_EVIDENCE_CAP, "未评估"
            missing.append(key)
            flags.append("missing")
        elif not evidence:
            s_eff, note = min(s, NO_EVIDENCE_CAP), "无证据封顶 2 分"
            flags.append("capped")
        else:
            s_eff, note = max(0.0, min(5.0, s)), ""

        if flags:
            capped.append(key)

        gained = s_eff / 5.0 * weight
        rows.append({
            "key": key, "dimension": dim, "item": text, "weight": weight,
            "raw_score": s, "effective_score": s_eff, "gained": round(gained, 2),
            "evidence": evidence, "note": note, "flags": flags,
        })
        d = dims.setdefault(dim, {"max": 0, "gained": 0.0, "items": 0})
        d["max"] += weight
        d["gained"] += gained
        d["items"] += 1

    total = round(sum(r["gained"] for r in rows), 1)
    dim_out = []
    for name, d in dims.items():
        dim_out.append({
            "dimension": name, "max": d["max"], "gained": round(d["gained"], 1),
            "pct": round(d["gained"] / d["max"] * 100, 1) if d["max"] else 0.0,
            "items": d["items"],
        })

    if total >= BAND_READY:
        band = "80–100 具备较好起点，但仍必须先做副业或低风险付费验证"
    elif total >= BAND_CONDITIONAL:
        band = "60–79 有条件适配，制定 3–6 个月短板修复计划"
    else:
        band = "0–59 当前不适配，优先获得真实案例、补充现金储备或训练销售"

    return {
        "total": total,
        "band": band,
        "dimensions": dim_out,
        "rows": rows,
        "capped_items": capped,
        "missing_items": missing,
        "capped_count": len(capped),
        "missing_count": len(missing),
        "note": "无事实记录的打分由引擎封顶 2 分（第三章 3.1）；评分不覆盖硬红线，也不替代真实付款证据。",
    }


# ══════════════════════════════════════════════════════════════════
# 闸门 5 · 单位经济（第六章 6.3、第九章）
# ══════════════════════════════════════════════════════════════════

def judge_econ(cfg):
    e = cfg.get("econ") or {}
    orders = e.get("orders") or []
    rate = num(e.get("target_hourly_rate"), 0.0) or 0.0
    cac = num(e.get("cac"))
    monthly_per_customer = num(e.get("monthly_contribution_per_customer"))

    if not orders:
        return {
            "level": "NO_DATA",
            "reason": "尚无交易数据，无法核算单位经济（不阻塞进入第二层，待有交易后回填）",
            "orders": [], "target_hourly_rate": rate,
            "blocking": False,
        }

    incomplete, loss, below, rows = [], [], [], []
    for idx, o in enumerate(orders, 1):
        rev = num(o.get("revenue"))
        cost = num(o.get("direct_cost"), 0.0) or 0.0
        hrs = num(o.get("hours"))
        tag = o.get("customer") or ("订单 %d" % idx)

        if rev is None or hrs is None:
            incomplete.append({"order": tag, "reason": "收入或工时缺失"})
            rows.append({"order": tag, "revenue": rev, "direct_cost": cost, "hours": hrs,
                         "contribution": None, "margin": None, "effective_hourly": None,
                         "status": "INCOMPLETE", "note": "有收入无工时记录，无法判断业务是否成立"})
            continue

        contrib = rev - cost - hrs * rate
        margin = (contrib / rev) if rev else None
        eff = (contrib / hrs) if hrs else None
        status = "OK"
        if contrib <= 0:
            status = "LOSS"
            loss.append(tag)
        elif eff is not None and rate and eff < rate:
            status = "BELOW_TARGET"
            below.append(tag)

        rows.append({
            "order": tag, "revenue": rev, "direct_cost": cost, "hours": hrs,
            "contribution": round(contrib, 2),
            "margin": round(margin * 100, 1) if margin is not None else None,
            "effective_hourly": round(eff, 2) if eff is not None else None,
            "status": status, "note": "",
        })

    if incomplete:
        level, reason, blocking = "INCOMPLETE", "%d 笔订单缺工时或收入，无法判定" % len(incomplete), True
    elif loss:
        level, reason, blocking = "LOSS", "%d 笔订单贡献利润为负" % len(loss), True
    elif below:
        level, reason, blocking = "BELOW_TARGET", "%d 笔订单有效时薪低于目标时薪" % len(below), False
    else:
        level, reason, blocking = "PASS", "每单贡献利润为正，有效时薪达到或超过目标", False

    agg_rev = sum(r["revenue"] for r in rows if r.get("revenue") is not None)
    agg_cost = sum(r["direct_cost"] for r in rows if r.get("revenue") is not None)
    agg_hours = sum(r["hours"] for r in rows if r.get("hours") is not None)
    agg_contrib = sum(r["contribution"] for r in rows if r.get("contribution") is not None)

    payback = None
    if cac is not None and monthly_per_customer:
        payback = round(cac / monthly_per_customer, 2)

    return {
        "level": level, "reason": reason, "blocking": blocking,
        "target_hourly_rate": rate,
        "orders": rows,
        "incomplete": incomplete, "loss_orders": loss, "below_target": below,
        "aggregate": {
            "revenue": round(agg_rev, 2),
            "direct_cost": round(agg_cost, 2),
            "hours": round(agg_hours, 1),
            "contribution": round(agg_contrib, 2),
            "contribution_margin": round(agg_contrib / agg_rev * 100, 1) if agg_rev else None,
            "effective_hourly": round(agg_contrib / agg_hours, 2) if agg_hours else None,
        },
        "cac": cac,
        "payback_periods": payback,
        "note": "有收入无工时记录时无法判断业务是否成立（第六章 6.3）。",
    }


# ══════════════════════════════════════════════════════════════════
# 伪验证信号拦截（第十二章 12.5）
# ══════════════════════════════════════════════════════════════════

def detect_signals(text):
    """伪验证信号拦截（第十二章 12.5）+ 信号强度分级（第一章 / 第五章 5.2）。"""
    text = text or ""
    low = text.lower()
    hits = []
    for key, surface, why, replace, kws in PSEUDO_SIGNALS:
        matched = [k for k in kws if k.lower() in low]
        if matched:
            hits.append({
                "key": key, "surface": surface, "why": why,
                "replace_with": replace, "matched_keywords": matched,
            })

    levels = []
    for lv, label, form, question, strong, kws in SIGNAL_LEVELS:
        matched = [k for k in kws if k.lower() in low]
        if matched:
            levels.append({"level": lv, "label": label, "form": form, "ask": question,
                           "is_strong": strong, "matched_keywords": matched})

    strongest = max((x["level"] for x in levels), default=None)
    return {
        "input": text,
        "hit_count": len(hits),
        "hits": hits,
        "level": "BLOCKED" if hits else "CLEAR",
        "levels": levels,
        "strongest_signal_level": strongest,
        "strongest_is_strong": bool(strongest == 3),
        "note": "关键词初筛，需人工确认语境。命中即不得作为已验证证据，必须换用右列指标（第十二章 12.5）。",
    }


def scan_signals(texts):
    """批量扫描多段描述，供报告使用。"""
    texts = [t for t in (texts or []) if str(t).strip()]
    if not texts:
        return {"scanned": 0, "pseudo_hits": [], "levels": [], "strongest_signal_level": None}
    pseudo, levels, strongest = [], [], None
    for t in texts:
        r = detect_signals(str(t))
        for h in r["hits"]:
            h = dict(h)
            h["source"] = str(t)
            pseudo.append(h)
        levels.extend(r["levels"])
        if r["strongest_signal_level"] is not None:
            strongest = r["strongest_signal_level"] if strongest is None \
                else max(strongest, r["strongest_signal_level"])
    return {
        "scanned": len(texts),
        "pseudo_hits": pseudo,
        "levels": levels,
        "strongest_signal_level": strongest,
        "strongest_is_strong": bool(strongest == 3),
    }


# ══════════════════════════════════════════════════════════════════
# 闸门 3 · 问题与客户（第四章 4.1/4.2 + 第五章 5.2/5.3）
# ══════════════════════════════════════════════════════════════════

VAGUE_LABEL_WORDS = ["咨询", "顾问", "代运营", "培训", "设计", "开发", "内容创作", "营销", "服务"]
SPECIFIC_MARKERS = ["卖家", "店主", "商家", "作者", "医生", "学员", "团队", "公司", "创业者", "客户",
                    "店主", "老师", "律师", "会计", "工厂", "卖家", "博主", "主播", "企业", "个人",
                    "跨境", "本地", "医院", "诊所", "门店", "工作室", "开发者", "运营者"]


def _label_check(name):
    """软提示：方向名不要写成宽泛能力标签，应写成「客户人群 + 使用场景 + 结果 + 交付形式」（第四章 4.1）。"""
    n = (name or "").strip()
    flags = []
    if len(n) < 10:
        flags.append("名称偏短，可能缺少人群与场景限定")
    if any(w in n for w in VAGUE_LABEL_WORDS) and not any(m in n for m in SPECIFIC_MARKERS):
        flags.append("含宽泛能力词但未见具体人群，建议改成「人群 + 场景 + 结果 + 交付形式」")
    return flags


def judge_direct(cfg):
    g3 = cfg.get("gate3") or {}
    dirs = g3.get("directions") or []
    if not dirs:
        return {
            "level": "NO_DATA",
            "reason": "未提供候选方向。方向必须从第四章 4.1 的四类证据出发生成，不得从模式目录反推。",
            "bases_reference": [{"key": k, "text": t} for k, t in DIRECTION_BASES],
            "directions": [], "qualified": [], "recommended": None,
        }

    rows = []
    for d in dirs:
        name = d.get("name") or "未命名方向"
        basis = d.get("basis")
        scores = d.get("scores") or {}
        items, floor_violations, gained, max_w = [], [], 0.0, 0
        for key, label, weight, question in DIRECTION_ITEMS:
            s = num(scores.get(key))
            s_eff = max(0.0, min(5.0, s)) if s is not None else None
            max_w += weight
            if s_eff is None:
                items.append({"key": key, "item": label, "weight": weight, "score": None,
                              "gained": None, "floor_violation": False})
                continue
            g = s_eff / 5.0 * weight
            gained += g
            viol = key in DIRECTION_HARD_FLOOR and s_eff < DIRECTION_FLOOR
            if viol:
                floor_violations.append({"key": key, "item": label, "score": s_eff})
            items.append({"key": key, "item": label, "weight": weight, "score": s_eff,
                          "gained": round(g, 2), "floor_violation": viol})
        total = round(gained, 1)
        missing = [i["key"] for i in items if i["score"] is None]
        qualified = (not floor_violations) and (not missing)
        rows.append({
            "name": name,
            "basis": basis,
            "basis_text": dict(DIRECTION_BASES).get(basis),
            "total": total,
            "items": items,
            "floor_violations": floor_violations,
            "missing_items": missing,
            "qualified": qualified,
            "label_flags": _label_check(name),
            "notes": d.get("notes") or "",
        })

    ranked = sorted(rows, key=lambda r: r["total"], reverse=True)
    qual = [r for r in ranked if r["qualified"]]
    return {
        "level": "OK" if qual else "NO_QUALIFIED",
        "reason": ("有 %d 个合格方向" % len(qual)) if qual
                  else "没有任何方向同时满足三项硬约束（合规与依赖风险 / 客户可触达性 / 交付可控性不得低于 3 分）",
        "bases_reference": [{"key": k, "text": t} for k, t in DIRECTION_BASES],
        "directions": ranked,
        "qualified": [r["name"] for r in qual],
        "recommended": qual[0]["name"] if qual else None,
        "screening_principles": [
            "竞争筛选：避开大厂可用标准化低价产品直接替代的场景，优先细分行业、复杂流程、强信任、需要判断的场景",
            "AI 筛选：AI 只降低检索、整理、生成和重复执行成本；事实核验、行业判断、客户沟通和结果责任仍由人承担",
            "轻交付筛选：启动成本低、范围清楚、交付周期短、验收标准明确，并且可逐步模板化",
        ],
        "note": "个人耐久度是约束条件，不是用来掩盖没有市场的理由（第四章 4.2）。",
    }


def judge_funnel(cfg):
    f = section(cfg, "funnel", "gate3")
    counts = {k: num(f.get(k)) for k, _l, _m, _p in FUNNEL_STAGES}
    for extra in ("advanced", "payers"):
        counts[extra] = num(f.get(extra))
    if all(v is None for v in counts.values()):
        return {
            "level": "NO_DATA",
            "reason": "未提供漏斗数据。预验证门槛需要 10–15 次有效访谈、至少 3 人愿意进一步、至少 1 人付款",
            "thresholds": [], "stages": [], "bottleneck": None,
        }

    def c(k):
        return counts.get(k) or 0

    thresholds = [
        {"key": "interviews", "label": "有效访谈",
         "required": "≥ %d 次（建议 %d）" % (PREVALIDATION["interviews_min"], PREVALIDATION["interviews_ideal"]),
         "actual": c("interviews"), "pass": c("interviews") >= PREVALIDATION["interviews_min"]},
        {"key": "advanced", "label": "愿意进一步（看方案 / 给资料 / 预约报价）",
         "required": "≥ %d 人" % PREVALIDATION["advanced_min"],
         "actual": c("advanced"), "pass": c("advanced") >= PREVALIDATION["advanced_min"]},
        {"key": "payers", "label": "支付定金或全款",
         "required": "≥ %d 人" % PREVALIDATION["payers_min"],
         "actual": c("payers"), "pass": c("payers") >= PREVALIDATION["payers_min"]},
    ]

    stages, prev = [], None
    for key, label, metric, purpose in FUNNEL_STAGES:
        v = counts.get(key)
        rate = None
        if prev and prev > 0 and v is not None:
            rate = round(v / prev * 100, 1)
        stages.append({"key": key, "label": label, "metric": metric, "purpose": purpose,
                       "value": v, "conversion_pct": rate})
        if v:
            prev = v

    drops = [s for s in stages if s["conversion_pct"] is not None]
    bottleneck = min(drops, key=lambda s: s["conversion_pct"]) if drops else None

    payers = c("payers")
    level = "PASS" if all(t["pass"] for t in thresholds) else (
        "NO_PAYMENT" if not thresholds[2]["pass"] else "BELOW_THRESHOLD")

    reason_map = {
        "PASS": "三项预验证门槛均已达到",
        "BELOW_THRESHOLD": "有付款但访谈量或愿意进一步的人数未达标",
        "NO_PAYMENT": "尚无真实付款。无法收款时应归因到具体环节，不要直接得出「市场不存在」",
    }
    return {
        "level": level,
        "reason": reason_map[level],
        "thresholds": thresholds,
        "stages": stages,
        "bottleneck": bottleneck,
        # 三种写法都接受：gate3.funnel.loss_reasons（规范）/ gate3.loss_reasons / 顶层 loss_reasons
        "loss_reasons": (f.get("loss_reasons")
                         or section(cfg, "loss_reasons", "gate3")
                         or {}),
        "loss_reason_options": LOSS_REASONS,
        "note": "没有付款，不应把方向称为已验证（第一章 / 第五章 5.2）。",
    }


# ══════════════════════════════════════════════════════════════════
# 闸门 4 · 付费与交付（第六章 6.1/6.2 + 第七章 7.2）
# ══════════════════════════════════════════════════════════════════

def judge_mso(cfg):
    m = section(cfg, "mso", "gate4")
    if not m:
        return {"level": "NO_DATA", "reason": "未提供最小可售 Offer（MSO）",
                "missing_fields": [l for _k, l in MSO_FIELDS], "standards": [],
                "pass_count": 0, "total_standards": 7}

    missing = [label for key, label in MSO_FIELDS if blank(m.get(key))]
    text_all = " ".join(str(m.get(k) or "") for k, _l in MSO_FIELDS)

    cycle = num(m.get("cycle_days"))
    price = num(m.get("price"))
    has_triplet = all(not blank(m.get(k)) for k in ("target_customer", "pain", "min_result"))
    has_io = all(not blank(m.get(k)) for k in ("deliverables", "client_inputs", "acceptance", "exclusions"))
    absolute = [w for w in ABSOLUTE_CLAIMS if w in text_all]
    contract_missing = [t for t in CONTRACT_TERMS if t not in text_all]

    standards = [
        {"no": 1, "text": "客户在 3–10 秒内能听懂服务对象、问题和结果",
         "pass": has_triplet, "detail": "" if has_triplet else "目标客户 / 最痛问题 / 最小结果 三项未齐备"},
        {"no": 2, "text": "交付周期通常不超过 14 天（除非较长周期是客户必要条件）",
         "pass": cycle is not None and cycle <= 14,
         "detail": "已填 %.0f 天" % cycle if cycle is not None else "未填交付周期"},
        {"no": 3, "text": "有明确的输入、输出、验收标准和不包含事项",
         "pass": has_io, "detail": "" if has_io else "交付物 / 客户输入 / 验收标准 / 不包含事项 有缺项"},
        {"no": 4, "text": "必须收费，免费项目不能替代市场验证",
         "pass": price is not None and price > 0,
         "detail": "已填价格 %.0f" % price if price is not None else "未填价格或价格为 0"},
        {"no": 5, "text": "单人可以手动完成，且不依赖客户无法控制的长期配合",
         "pass": not blank(m.get("delivery_hours")),
         "detail": "" if not blank(m.get("delivery_hours")) else "未填预计交付工时，无法判断单人可控性"},
        {"no": 6, "text": "不承诺无法控制的结果，不使用保证类高风险表述",
         "pass": not absolute,
         "detail": "" if not absolute else "命中绝对化表述：%s" % "、".join(absolute)},
        {"no": 7, "text": "对定金、变更、延期、退款、保密、成果归属和售后范围有书面约定",
         "pass": not contract_missing,
         "detail": "" if not contract_missing else "未提及：%s" % "、".join(contract_missing)},
    ]
    passed = sum(1 for s in standards if s["pass"])
    level = "OK" if (passed == len(standards) and not missing) else (
        "INCOMPLETE" if missing else "NOT_READY")
    return {
        "level": level,
        "reason": ("7 条最低标准全部满足，19 个字段齐备" if level == "OK" else
                   ("缺 %d 个必填字段" % len(missing) if missing else
                    "标准通过 %d/7，未达可售门槛" % passed)),
        "missing_fields": missing,
        "field_count": len(MSO_FIELDS),
        "standards": standards,
        "pass_count": passed,
        "total_standards": len(standards),
        "note": "必须收费。免费项目只能用于内部练习，不能替代市场验证（第六章 6.2）。",
    }


def judge_delivery(cfg):
    """首 3 个客户的验证标准 · 第七章 7.2。"""
    d = section(cfg, "delivery", "gate4")
    if not d:
        return {"level": "NO_DATA", "reason": "未提供交付结果记录", "checks": [], "pass_count": 0}

    checks = [
        ("payment", "至少 1 个客户真实付款，最好累计 2–3 个不同客户的付款证据",
         (num(d.get("paying_customers")) or 0) >= 1,
         "付款客户数 %s" % (d.get("paying_customers") if d.get("paying_customers") is not None else "未填")),
        ("scope_control", "单人独立完成交付，且交付范围没有失控",
         d.get("scope_controlled") is True, ""),
        ("positive_econ", "单客贡献利润为正，有效时薪没有持续低于个人可接受下限",
         d.get("econ_positive") is True, ""),
        ("client_feedback", "客户对结果、过程或交付体验给出具体正面反馈",
         not blank(d.get("feedback")), ""),
        ("follow_on", "至少有 1 个复购、续费、转介绍或明确的后续需求信号",
         (num(d.get("follow_ons")) or 0) >= 1,
         "复购/转介绍 %s" % (d.get("follow_ons") if d.get("follow_ons") is not None else "未填")),
        ("compliant", "业务不依赖违法、侵权、未经授权的数据或单一平台",
         d.get("compliant") is True, ""),
        ("willingness", "经过首单后，创业者仍愿意继续承担销售、交付和售后",
         d.get("still_willing") is True, ""),
    ]
    rows = [{"key": k, "text": t, "pass": bool(p), "detail": det} for k, t, p, det in checks]
    passed = sum(1 for r in rows if r["pass"])
    return {
        "level": "PASS" if passed == len(rows) else "PARTIAL",
        "reason": "首 3 个客户验证标准通过 %d/%d" % (passed, len(rows)),
        "checks": rows, "pass_count": passed, "total_checks": len(rows),
        "note": "以上条件建议至少满足后才考虑增加投入（第七章 7.2）。",
    }


# ══════════════════════════════════════════════════════════════════
# 止损与退出监控（第二章 2.3 + 第七章 7.3 + 第十章）
# ══════════════════════════════════════════════════════════════════

def judge_stop(cfg):
    t = section(cfg, "tracking")
    g1 = cfg.get("gate1") or {}
    stop_loss = g1.get("stop_loss") or {}
    if not t:
        return {"level": "NO_DATA", "reason": "未提供试跑跟踪数据",
                "immediate": [], "suggest": [], "rounds_used": 0,
                "verdict": "NO_DATA", "adjust_order": ADJUST_ORDER,
                "alt_paths": [{"key": k, "path": p, "fits": f} for k, p, f in ALT_PATHS]}

    cash_spent = num(t.get("cash_spent"))
    cash_limit = num(t.get("cash_limit"))
    weeks = num(t.get("weeks_elapsed"))
    weeks_limit = num(t.get("weeks_limit"))
    rounds = t.get("rounds") or []

    immediate = [{"key": k, "text": txt, "hit": (t.get("immediate") or {}).get(k) is True}
                 for k, txt in STOP_IMMEDIATE]
    imm_hits = [x for x in immediate if x["hit"]]

    suggest = [{"key": k, "text": txt, "hit": (t.get("suggest") or {}).get(k) is True}
               for k, txt in STOP_SUGGEST]
    sug_hits = [x for x in suggest if x["hit"]]

    over_cash = cash_limit is not None and cash_spent is not None and cash_spent > cash_limit
    over_weeks = weeks_limit is not None and weeks is not None and weeks > weeks_limit
    total_payments = sum((num(r.get("payments")) or 0) for r in rounds)
    bad_rounds = [r for r in rounds if len(r.get("changed_vars") or []) > MAX_VARS_PER_ROUND]

    if imm_hits:
        verdict, reason = "EXIT_NOW", "命中立即停止条件：%s" % "；".join(x["text"] for x in imm_hits)
    elif len(sug_hits) >= 2:
        verdict, reason = "STOP", "命中建议停止/转向条件 %d 项（≥2）：%s" % (
            len(sug_hits), "；".join(x["text"] for x in sug_hits))
    elif len(rounds) >= MAX_ROUNDS and total_payments <= 0:
        verdict, reason = "EXIT", "已完成 %d 轮验证（上限 %d）且无任何付款记录" % (len(rounds), MAX_ROUNDS)
    elif over_cash or over_weeks:
        verdict, reason = "PAUSE", "已触及止损线：%s%s" % (
            "现金投入超限 " if over_cash else "", "试跑时间超限" if over_weeks else "")
    elif total_payments > 0:
        verdict, reason = "KEEP", "已有 %d 笔付款记录，交付与单位经济可继续观察" % total_payments
    else:
        verdict, reason = "ADJUST", "尚无付款证据，按调整顺序改变 1–2 个关键变量后重试"

    return {
        "level": verdict,
        "verdict": verdict,
        "reason": reason,
        "immediate": immediate, "immediate_hits": len(imm_hits),
        "suggest": suggest, "suggest_hits": len(sug_hits),
        "rounds_used": len(rounds), "rounds_limit": MAX_ROUNDS, "round_days": ROUND_DAYS,
        "rounds_over_vars": [r.get("round") for r in bad_rounds],
        "max_vars_per_round": MAX_VARS_PER_ROUND,
        "cash_spent": cash_spent, "cash_limit": cash_limit,
        "weeks_elapsed": weeks, "weeks_limit": weeks_limit,
        "stop_loss_text": stop_loss,
        "adjust_order": ADJUST_ORDER,
        "alt_paths": [{"key": k, "path": p, "fits": f} for k, p, f in ALT_PATHS],
        "note": "每轮只能明确改变一到两个关键变量；两轮仍无付款证据时进入复评、转向或退出（第七章 7.3）。",
    }


# ══════════════════════════════════════════════════════════════════
# 第一层合成结论（第一/三/四/五/六/七/十/十二章）
# ══════════════════════════════════════════════════════════════════

def judge_assay(cfg):
    has_score = bool((cfg.get("score") or {}).get("items"))
    gate1 = judge_gate1(cfg)
    score = judge_score(cfg) if has_score else None
    econ = judge_econ(cfg)
    direct = judge_direct(cfg)
    funnel = judge_funnel(cfg)
    mso = judge_mso(cfg)
    delivery = judge_delivery(cfg)
    stop = judge_stop(cfg)

    reasons, gaps, caps = [], [], []
    if not has_score:
        return {
            "level": "INCOMPLETE",
            "level_text": ASSAY_LEVELS["INCOMPLETE"],
            "reason": "缺少必填输入：适配度评分（闸门 2）",
            "gate1": gate1, "score": None, "econ": econ,
            "gate3": {"direct": direct, "funnel": funnel},
            "gate4": {"mso": mso, "delivery": delivery},
            "stop": stop,
            "gaps": ["闸门 2 未评估"], "repairs": [], "next_actions": [],
            "may_enter_layer2": False, "scale_gate": {"pass": False, "items": []},
            "evidence_rule": EVIDENCE_RULE,
        }

    if gate1["level"] == "LOCKED":
        level = "LOCKED"
        for h in gate1["redline_hits"]:
            reasons.append("硬红线：%s" % h["text"])
    elif gate1["level"] == "INCOMPLETE":
        level = "INCOMPLETE"
        reasons.append(gate1["reason"])
    elif score["total"] < BAND_CONDITIONAL:
        level = "INSUFFICIENT"
        reasons.append("适配度 %s 分，低于 60 分" % score["total"])
    elif score["total"] < BAND_READY or gate1["soft_overlay_triggered"]:
        level = "CONDITIONAL"
        if score["total"] < BAND_READY:
            reasons.append("适配度 %s 分，处于 60–79 区间" % score["total"])
        if gate1["soft_overlay_triggered"]:
            reasons.append("软风险命中 %d 项（≥3），即使高分也仅限副业验证（第三章 3.3）" % gate1["soft_count"])
    else:
        level = "READY_FOR_LAYER2"
        reasons.append("无硬红线、软风险 <3、适配度 %s 分" % score["total"])

    def cap_at(ceil, why):
        before = ASSAY_RANK[level]
        if before > ASSAY_RANK[ceil]:
            caps.append(why)
            return ceil
        return level

    if econ.get("blocking"):
        reasons.append("单位经济：%s" % econ["reason"])
        level = cap_at("CONDITIONAL", "单位经济阻断：%s" % econ["reason"])

    if direct["level"] == "NO_DATA":
        level = cap_at("CONDITIONAL", "闸门 3 未评估（没有候选方向）")
    elif direct["level"] == "NO_QUALIFIED":
        reasons.append("闸门 3：%s" % direct["reason"])
        level = cap_at("INSUFFICIENT", "闸门 3 无合格方向")
    elif funnel["level"] == "NO_DATA":
        level = cap_at("CONDITIONAL", "闸门 3 验证数据未评估（漏斗为空）")
    elif funnel["level"] == "NO_PAYMENT":
        level = cap_at("CONDITIONAL", "闸门 3/4：尚无真实付款证据")
    elif funnel["level"] == "BELOW_THRESHOLD":
        level = cap_at("CONDITIONAL", "闸门 3：预验证门槛未达")

    if mso["level"] == "NO_DATA":
        level = cap_at("CONDITIONAL", "闸门 4 未评估（没有 MSO）")
    elif mso["level"] in ("INCOMPLETE", "NOT_READY"):
        reasons.append("闸门 4：%s" % mso["reason"])
        level = cap_at("CONDITIONAL", "闸门 4：MSO %s" % mso["reason"])

    if stop["verdict"] in ("EXIT_NOW", "STOP", "EXIT"):
        reasons.append("止损监控：%s" % stop["reason"])
        level = cap_at("INSUFFICIENT", "止损监控触发 %s" % stop["verdict"])
    elif stop["verdict"] == "PAUSE":
        reasons.append("止损监控：%s" % stop["reason"])
        level = cap_at("CONDITIONAL", "止损监控触发 PAUSE")

    if score["capped_count"]:
        gaps.append("适配度评分中有 %d 项无证据，已按 2 分封顶" % score["capped_count"])
    if gate1["soft_count"]:
        gaps.append("软风险命中 %d 项：%s" % (
            gate1["soft_count"], "、".join(h["text"] for h in gate1["soft_hits"])))
    if gate1["cushion_months"] is not None:
        gaps.append("安全垫 %.1f 个月（目标 %s 个月）" % (
            gate1["cushion_months"], gate1["cushion_target_months"] or "未设定"))
    gaps.extend("止损数字未写：%s" % k for k in gate1["stop_loss_missing"])
    if econ["level"] == "NO_DATA":
        gaps.append("尚无交易数据，单位经济待回填")
    if direct["level"] == "NO_DATA":
        gaps.append("闸门 3 未评估：需要从四类证据出发生成 3–5 个候选方向")
    for d in direct.get("directions", []):
        if d["floor_violations"]:
            gaps.append("方向「%s」有 %d 项低于 3 分的硬约束：%s" % (
                d["name"], len(d["floor_violations"]),
                "、".join(v["item"] for v in d["floor_violations"])))
        for f in d.get("label_flags", []):
            gaps.append("方向「%s」命名待改：%s" % (d["name"], f))
    if funnel["level"] == "NO_DATA":
        gaps.append("闸门 3 验证数据未评估：访谈数、愿意进一步人数、付款人数均未记录")
    elif funnel["level"] != "PASS":
        for t in funnel["thresholds"]:
            if not t["pass"]:
                gaps.append("预验证门槛未达：%s（要求 %s，实际 %s）" % (t["label"], t["required"], t["actual"]))
        if funnel.get("bottleneck"):
            b = funnel["bottleneck"]
            gaps.append("漏斗瓶颈在「%s」环节，转化率 %s%%" % (b["label"], b["conversion_pct"]))
    if mso["level"] == "NO_DATA":
        gaps.append("闸门 4 未评估：需要一份一页纸 MSO")
    elif mso["missing_fields"]:
        gaps.append("MSO 缺 %d 个必填字段：%s" % (len(mso["missing_fields"]), "、".join(mso["missing_fields"])))
    for s in mso.get("standards", []):
        if not s["pass"]:
            gaps.append("MSO 第 %d 条不满足：%s（%s）" % (s["no"], s["text"], s["detail"]))
    if delivery["level"] == "NO_DATA":
        gaps.append("交付结果未评估：首 3 个客户验证标准待记录")

    repairs = []
    for r in score["rows"]:
        if r["effective_score"] <= 2 and r["key"] in REPAIR_MAP:
            area, action, term, proof = REPAIR_MAP[r["key"]]
            repairs.append({"shortfall": area, "action": action, "term": term,
                            "proof": proof, "item": r["item"], "effective_score": r["effective_score"]})

    nxt = []
    if level == "LOCKED":
        nxt = ["仅在不影响主业、家庭和健康的前提下做低成本测试",
               "逐条处理命中的硬红线；无法处理时更换业务方向"]
    elif level == "INCOMPLETE":
        nxt = ["补齐止损三数字：最大现金投入、最长试跑时间、最低继续结果"] + \
              (["补充 %d 项评分的证据记录" % score["capped_count"]] if score["capped_count"] else [])
    elif level == "INSUFFICIENT":
        nxt = ["按短板修复计划补足能力与资源证据"]
        if direct["level"] == "NO_QUALIFIED":
            nxt.append("重新生成候选方向：三项硬约束（合规与依赖风险 / 客户可触达性 / 交付可控性）都不得低于 3 分")
        if stop["verdict"] in ("STOP", "EXIT", "EXIT_NOW"):
            nxt.append("按退出与替代路径评估：保留复盘后切换路径，不继续凭意志追加投入")
        else:
            nxt.append("优先取得 1 个真实付费客户与完整回款")
    elif level == "CONDITIONAL":
        nxt = ["按短板修复计划执行，修复期内不全职启动"]
        if score["capped_count"]:
            nxt.append("补齐 %d 项无证据评分项的事实记录" % score["capped_count"])
        if mso.get("missing_fields"):
            nxt.append("补齐 MSO 的 %d 个必填字段" % len(mso["missing_fields"]))
        if funnel["level"] in ("NO_DATA", "NO_PAYMENT", "BELOW_THRESHOLD"):
            nxt.append("完成 10–15 次目标客户访谈，并尝试收取第一笔定金或全款")
        if stop["verdict"] == "ADJUST":
            nxt.append("按调整顺序改变 1–2 个关键变量后重试（最多 %d 轮，每轮约 %d 天）"
                       % (stop["rounds_limit"], stop["round_days"]))
    else:
        nxt = ["进入第二层：确定 OPC 模式，激活该模式的专属指标",
               "选定北极星指标 1 个、过程指标 2 个、安全指标 2 个，进入周期记录"]

    # 扩大投入闸门 · 第十一章末段：真实付款 + 可控交付 + 单位经济为正 + 风险可控
    paid = (funnel["level"] in ("PASS", "BELOW_THRESHOLD")) or \
           (num(delivery.get("pass_count")) or 0) > 0 or bool((cfg.get("econ") or {}).get("orders"))
    scale_items = [
        {"key": "paid", "label": "真实付款", "pass": paid,
         "detail": "漏斗付款人数 %s" % (funnel["thresholds"][2]["actual"] if funnel.get("thresholds") else "未记录")},
        {"key": "delivery", "label": "可控交付", "pass": delivery.get("level") == "PASS",
         "detail": delivery.get("reason") or ""},
        {"key": "econ", "label": "单位经济为正", "pass": econ.get("level") == "PASS",
         "detail": econ.get("reason") or ""},
        {"key": "risk", "label": "风险可控",
         "pass": gate1["redline_count"] == 0 and not gate1["soft_overlay_triggered"],
         "detail": "硬红线 %d 条 · 软风险 %d 项" % (gate1["redline_count"], gate1["soft_count"])},
    ]
    scale_gate = {"pass": all(i["pass"] for i in scale_items), "items": scale_items}

    return {
        "level": level,
        "level_text": ASSAY_LEVELS[level],
        "reason": "；".join(reasons) or "无阻断项",
        "caps": caps,
        "gate1": gate1, "score": score, "econ": econ,
        "gate3": {"direct": direct, "funnel": funnel},
        "gate4": {"mso": mso, "delivery": delivery},
        "stop": stop,
        "gaps": gaps, "repairs": repairs, "next_actions": nxt,
        "may_enter_layer2": level == "READY_FOR_LAYER2",
        "scale_gate": scale_gate,
        "evidence_rule": EVIDENCE_RULE,
    }


# ══════════════════════════════════════════════════════════════════
# 状态档案（跨会话续跑，无任何定时能力）
# ══════════════════════════════════════════════════════════════════

STAGES = ["inited", "layer1_gate1", "layer1_score", "layer1_econ", "layer1_done",
          "layer2_modes", "layer2_metrics", "layer2_board"]

STAGE_LABEL = {
    "inited": "未开始",
    "layer1_gate1": "第一层 · 硬红线与止损自查中",
    "layer1_score": "第一层 · 适配度评分中",
    "layer1_econ": "第一层 · 单位经济核算中",
    "layer1_done": "第一层完成",
    "layer2_modes": "第二层 · 模式归类中",
    "layer2_metrics": "第二层 · 专属指标选定中",
    "layer2_board": "第二层 · 周期记录中",
}


def state_path(cfg, explicit=None):
    if explicit:
        return explicit
    s = cfg.get("state") or {}
    return s.get("path") or os.path.join(os.getcwd(), "opc-bootstrap-state.json")


def state_read(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except ValueError:
            pass
    return None


def state_init(cfg, path):
    name = ((cfg.get("user") or {}).get("name") or "").strip()
    return {
        "schema": SCHEMA,
        "engine_version": VERSION,
        "created": today(),
        "updated": today(),
        "stage": "inited",
        "founder": name or "未署名",
        "audience": (cfg.get("user") or {}).get("audience") or "external",
        "layer1": {"gate1": None, "score": None, "gate3_direct": None, "gate3_funnel": None,
                   "gate4_mso": None, "econ": None, "assay": None},
        "layer2": {"mode": None, "metric_pack": None, "slots": None, "records": []},
        "evidence_log": [],
        "notes": [],
    }


def state_write(st, cfg):
    st["updated"] = today()
    st["engine_version"] = VERSION
    path = state_path(cfg)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path


def state_apply(st, cfg, stage=None, note=None, layer2=None):
    l1 = st.setdefault("layer1", {})
    if cfg.get("gate1"):
        l1["gate1"] = {"level": judge_gate1(cfg)["level"], "checked": today()}
    if (cfg.get("score") or {}).get("items"):
        l1["score"] = {"total": judge_score(cfg)["total"], "checked": today()}
    if (cfg.get("gate3") or {}).get("directions"):
        d = judge_direct(cfg)
        l1["gate3_direct"] = {"level": d["level"], "recommended": d.get("recommended"),
                              "checked": today()}
    if (cfg.get("gate3") or {}).get("funnel"):
        f = judge_funnel(cfg)
        l1["gate3_funnel"] = {"level": f["level"], "checked": today()}
    if (cfg.get("gate4") or {}).get("mso"):
        l1["gate4_mso"] = {"level": judge_mso(cfg)["level"], "checked": today()}
    if (cfg.get("econ") or {}).get("orders"):
        l1["econ"] = {"level": judge_econ(cfg)["level"], "checked": today()}
    if (cfg.get("score") or {}).get("items"):
        a = judge_assay(cfg)
        l1["assay"] = {"level": a["level"], "scale_gate": a["scale_gate"]["pass"],
                       "checked": today()}
    if layer2:
        if not isinstance(layer2, dict):
            die("--layer2 需要是 JSON 对象")
        st.setdefault("layer2", {}).update(layer2)
    if note:
        st["notes"].append({"date": today(), "text": note})
    if stage:
        if stage not in STAGES:
            die("未知阶段：%s（可选：%s）" % (stage, ", ".join(STAGES)))
        st["stage"] = stage
    else:
        st["stage"] = infer_stage(st)
    return st


def infer_stage(st):
    l1 = st.get("layer1") or {}
    if l1.get("assay"):
        if not l1["assay"].get("level") == "READY_FOR_LAYER2":
            return "layer1_done"
        l2 = st.get("layer2") or {}
        if l2.get("slots"):
            return "layer2_board"
        if l2.get("metric_pack"):
            return "layer2_metrics"
        return "layer2_modes"
    if l1.get("econ"):
        return "layer1_econ"
    if l1.get("gate4_mso") or l1.get("gate3_funnel"):
        return "layer1_score"
    if l1.get("gate3_direct") or l1.get("score"):
        return "layer1_score"
    if l1.get("gate1"):
        return "layer1_gate1"
    return "inited"


# ══════════════════════════════════════════════════════════════════
# 第二层：模式归类 + 合并看板
# 出处：第十二章 12.2 模式地图 / 12.3 专属指标 / 12.4 看板用法
#       + 第九章 9.1 每周经营看板 / 9.2 继续调整暂停决策表
# ══════════════════════════════════════════════════════════════════

MODES_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "assets", "modes.json"))

_MODES_CACHE = {}

# 顺序违规 / 前置条件未满足的退出码（与 0 成功、2 输入错误区分）
RC_ORDER = 4


def load_modes(path=None):
    """读取第二层模式库，并建立 key 索引（含缓存）。"""
    p = os.path.abspath(path or os.environ.get("OPC_MODES_PATH") or MODES_PATH)
    if p in _MODES_CACHE:
        return _MODES_CACHE[p]
    if not os.path.exists(p):
        die("找不到第二层模式库：%s（应由 assets/modes.json 提供）" % p)
    with open(p, encoding="utf-8") as fh:
        d = json.load(fh)

    mode_index, pack_index = {}, {}
    for cat in d.get("categories", []):
        for m in cat.get("modes", []):
            mm = dict(m)
            mm["category"] = cat.get("key")
            mm["category_name"] = cat.get("name")
            mode_index[mm["key"]] = mm
    for pk in d.get("metric_packs", []):
        pack_index[pk["key"]] = pk

    missing = [k for k, m in mode_index.items() if m.get("pack") not in pack_index]
    if missing:
        die("模式库自检失败：这些模式指向了不存在的指标包 %s" % "、".join(missing))

    d["_mode_index"] = mode_index
    d["_pack_index"] = pack_index
    _MODES_CACHE[p] = d
    return d


_SPLIT_RE = re.compile(r"[、，,；;／/|]+")


def _split(s):
    return [x.strip() for x in _SPLIT_RE.split(s or "") if x.strip()]


def _mode_keywords(m):
    """模式的可匹配关键词：模式名 + slug + 别名 + 典型形式逐项（长度 ≥ 2）。"""
    kws = set()
    for v in [m.get("name"), m.get("slug")] + list(m.get("aliases") or []):
        if v:
            kws.add(str(v).strip().lower())
    for x in _split(m.get("forms")):
        kws.add(x.lower())
    return sorted((k for k in kws if len(k) >= 2), key=len, reverse=True)


def _kw_df(mods):
    """关键词的文档频率：出现在多少个模式的词表里。用于压制"模板""服务"这类通用词。"""
    if "_kw_df" not in mods:
        df = {}
        for m in mods["_mode_index"].values():
            for kw in set(_mode_keywords(m)):
                df[kw] = df.get(kw, 0) + 1
        mods["_kw_df"] = df
    return mods["_kw_df"]


def _kw_idf(df, total):
    """平滑 IDF ∈ [1, ~4.5]：越通用的词权重越低。"""
    return math.log((total + 1.0) / (df + 1.0)) + 1.0


# 方向描述相对"主要收入来源"的权重折扣：方向讲的是做什么事，不完全等于怎么赚钱
WEAK_TEXT_WEIGHT = 0.4
# 只凭弱信号自动归类的门槛：绝对得分 + 相对领先倍数
WEAK_MIN_SCORE = 14.0
WEAK_LEAD_RATIO = 2.0


def resolve_mode(primary, mods=None, weak=None, explicit=None):
    """把业务描述归到具体模式。

    `primary` = 主要收入来源 + 主要风险来源 + 业务描述（第十二章 12.2 末段的定模式依据）。
    `weak`    = 方向名称，只作为打折后的补充信号（同一个词在弱文本里权重 ×0.4）。

    关键词权重 = 词长 × IDF，避免"模板""咨询"这类高频通用词把结果带偏；
    只有**完整模式名或 slug**出现才给整名加分，短别名不再无条件加分。
    """
    mods = mods or load_modes()
    idx = mods["_mode_index"]

    if explicit:
        e = str(explicit).strip()
        m = idx.get(e.upper()) or idx.get(e)
        if not m:
            for cand in idx.values():
                if cand["slug"] == e or cand["slug"] == e.lower() or cand["name"] == e:
                    m = cand
                    break
        if m:
            return {"ok": True, "mode": m, "matched_by": "explicit", "confidence": "high",
                    "keyword": e, "score": None, "ambiguous": False, "candidates": []}

    strong = (primary or "").strip().lower()
    soft = (weak or "").strip().lower()
    if not strong and not soft:
        return {"ok": False, "mode": None, "matched_by": "none", "confidence": "none",
                "keyword": None, "score": None, "ambiguous": False, "candidates": []}

    total = len(idx)
    df = _kw_df(mods)
    scored = []
    for key, m in idx.items():
        hits, score = [], 0.0
        for kw in _mode_keywords(m):
            w = len(kw) * _kw_idf(df.get(kw, 0), total)
            if strong and kw in strong:
                hits.append(kw)
                score += w
            elif soft and kw in soft:
                hits.append(kw + "（弱）")
                score += w * WEAK_TEXT_WEIGHT
        if not hits:
            continue
        if strong and m["name"].lower() in strong:
            score += 20.0
        if strong and m["slug"] in strong:
            score += 10.0
        scored.append({"key": key, "name": m["name"], "score": round(score, 1),
                       "hits": hits[:6], "pack": m["pack"], "category": m["category"]})

    scored.sort(key=lambda x: (-x["score"], x["key"]))
    cands = scored[:3]
    if not cands or cands[0]["score"] <= 0:
        return {"ok": False, "mode": None, "matched_by": "none", "confidence": "none",
                "keyword": None, "score": None, "ambiguous": False, "candidates": []}

    top = cands[0]
    second = cands[1]["score"] if len(cands) > 1 else 0.0
    ambiguous = second > 0 and second >= top["score"] * 0.85
    if strong:
        confidence = "medium" if ambiguous else "high"
    else:
        confidence = ("low" if (top["score"] >= WEAK_MIN_SCORE
                                and (second <= 0 or top["score"] >= second * WEAK_LEAD_RATIO))
                      else "none")
    return {"ok": confidence != "none", "mode": idx[top["key"]],
            "matched_by": "keyword" if strong else "weak_keyword",
            "keyword": top["hits"][0] if top["hits"] else None,
            "score": top["score"], "confidence": confidence,
            "ambiguous": ambiguous, "candidates": cands}


def judge_modes(cfg, mods=None):
    """第二层第一步：把已产出的候选方向归到 OPC 模式，并取出该模式的专属指标包。

    两道硬约束：
    1. **模式目录不得反用于选题**（第十二章 12.2 末段）：没有合格候选方向时直接拒绝。
    2. **第一模式由"主要收入来源 + 主要风险来源"决定**，方向名称只是打折后的弱信号。
       只给了方向名时，除非弱信号领先明显（≥2 倍），否则不自动归类，而是回问收入来源。
    """
    mods = mods or load_modes()
    l2 = cfg.get("layer2") or {}
    direct = judge_direct(cfg)
    qualified = [d for d in direct.get("directions", []) if d.get("qualified")]

    if not qualified:
        return {
            "ok": False,
            "level": "BLOCKED_ORDER",
            "reason": "模式目录不得反用于选题：必须先产出合格候选方向，再用模式地图归类。",
            "detail": "闸门 3 当前状态 %s，没有同时满足三项硬约束（合规与依赖风险 / 客户可触达性 / 交付可控性 ≥ 3 分）的方向。"
                      % direct.get("level"),
            "required": "gate3.directions[] 中至少一个方向合格",
            "next_action": "先跑 direct：从第四章四类证据（过去做成的 / 别人反复来问的 / 市场已在花钱解决的 / 14 天能交付的）"
                           "生成 3–5 个候选方向，完成 9 项评分后回到本命令。",
            "source": "第十二章 12.2 末段 · 第四章 4.1",
            "categories_overview": [{"key": c["key"], "name": c["name"],
                                     "mode_count": len(c["modes"])} for c in mods["categories"]],
        }

    chosen = l2.get("direction") or qualified[0]["name"]
    matched_direction = next(
        (d["name"] for d in qualified
         if chosen and (str(chosen) in d["name"] or d["name"] in str(chosen))), None)

    rev = l2.get("revenue_source")
    risk = l2.get("risk_source")
    desc = l2.get("description")
    primary = " ".join(str(p) for p in (rev, risk, desc) if p)
    r = resolve_mode(primary, mods, weak=str(chosen),
                     explicit=l2.get("mode_key") or l2.get("mode"))

    out = {
        "ok": bool(r["ok"]),
        "level": "RESOLVED" if r["ok"] else ("NEEDS_INPUT" if not primary.strip() else "UNRESOLVED"),
        "direction": chosen,
        "direction_in_candidates": bool(matched_direction),
        "qualified_directions": [d["name"] for d in qualified],
        "revenue_source": rev,
        "risk_source": risk,
        "matched_by": r["matched_by"],
        "confidence": r["confidence"],
        "keyword": r.get("keyword"),
        "score": r.get("score"),
        "ambiguous": r.get("ambiguous", False),
        "candidates": r.get("candidates", []),
        "mode": None, "category": None, "pack": None, "overlays": [], "slot_pool": None,
        "rule": "一个业务可能同时属于多个类别时，按**主要收入来源 + 主要风险来源**确定第一模式，"
                "其余模式只作为叠加模式取指标（第十二章 12.2 末段）。",
        "source": "第十二章 12.2 模式地图 / 12.3 专属指标",
    }

    if not r["ok"]:
        if out["level"] == "NEEDS_INPUT":
            out["ask"] = "你的钱主要从哪来？（例如：客户按项目付款 / 订阅付费 / 平台佣金 / 卖数字产品 / 广告与赞助）"
            out["reason"] = ("第一模式必须由主要收入来源决定，方向名称只是弱信号。"
                             "当前只有方向描述，弱信号不足以自动归类。")
            out["next_action"] = ("补充 layer2.revenue_source（+ layer2.risk_source），"
                                  "或从候选里直接用 layer2.mode_key 指定。")
            out["mode_index"] = [{"key": m["key"], "name": m["name"], "category": m["category"]}
                                 for m in mods["_mode_index"].values()]
        else:
            out["reason"] = "无法把描述归到任何模式：描述里没有任何可匹配的业务形态关键词。"
            out["next_action"] = ("补充 layer2.description（你在卖什么、卖给谁、钱从哪来），"
                                  "或直接用 layer2.mode_key 指定模式 key。")
            out["mode_index"] = [{"key": m["key"], "name": m["name"], "category": m["category"]}
                                 for m in mods["_mode_index"].values()]
        return out

    m = r["mode"]
    pack = mods["_pack_index"][m["pack"]]
    overlay_keys = l2.get("overlays")
    if overlay_keys is None:
        overlay_keys = m.get("default_overlays") or []
    overlays = []
    for k in overlay_keys:
        pkt = (mods["_pack_index"].get(str(k).strip().upper())
               or mods["_pack_index"].get(str(k).strip()))
        if pkt and pkt["key"] != pack["key"]:
            overlays.append({"key": pkt["key"], "name": pkt["name"], "pack": pkt})

    out["mode"] = {"key": m["key"], "slug": m["slug"], "name": m["name"],
                   "forms": m.get("forms"), "note": m.get("note", "")}
    out["category"] = {"key": m["category"], "name": m["category_name"]}
    out["pack"] = pack
    out["overlays"] = overlays
    # 12.4：不追全表，从指标包里挑 1 北极星 + 2 过程 + 2 安全
    out["slot_pool"] = {
        "north_star": [pack["signal"]],
        "process": _split(pack["ops"]),
        "safety": _split(pack["econ"]) + _split(pack["risk"]),
    }
    if out["confidence"] == "low":
        out["warning"] = ("仅凭方向描述推断（弱信号），请确认模式是否正确；"
                          "确认方式是补上主要收入来源，或用 mode_key 指定。")
    if out["ambiguous"]:
        out["warning"] = "匹配结果不唯一，前两名得分接近，请与用户确认后再锁指标。"
    if matched_direction is None:
        out["warning"] = ("layer2.direction=%r 不在本次合格候选方向里，已按该描述归类；"
                          "若方向本身还没过闸门 3，第二层结论不成立。" % chosen)
    return out


def decide_board(assay, modes_res, cfg, mods=None):
    """第九章 9.2 继续 / 调整 / 暂停决策表 —— 按顺序命中第一条。"""
    mods = mods or load_modes()
    l2 = cfg.get("layer2") or {}
    stop = assay.get("stop") or {}
    econ = assay.get("econ") or {}
    funnel = (assay.get("gate3") or {}).get("funnel") or {}
    gate1 = assay.get("gate1") or {}
    verdict = stop.get("verdict")
    conc = num(l2.get("concentration"))
    cycles_paid = num(l2.get("cycles_paid")) or 0
    payment_exists = funnel.get("level") in ("PASS", "BELOW_THRESHOLD")

    table = {d["key"]: d for d in mods["decision_table"]}

    def pack(key, basis):
        row = table[key]
        return {"key": key, "when": row["when"], "decision": row["decision"],
                "basis": basis, "source": "第九章 9.2 决策表"}

    if verdict == "EXIT_NOW":
        return pack("downgrade", "命中立即停止全职投入条件（第十章 10.1）：%s" % stop.get("reason"))
    if gate1.get("level") == "LOCKED":
        return pack("downgrade", "命中硬红线，只允许低成本副业测试（第二章 2.1）")
    if verdict == "PAUSE":
        return pack("downgrade", "已触及止损线（现金或时间超限）：%s" % stop.get("reason"))
    if verdict in ("STOP", "EXIT"):
        return pack("recycle", "建议停止/转向或两轮无付款证据（第十章 10.2 / 第七章 7.3）：%s" % stop.get("reason"))
    # 无付款是生存问题，优先于"集中度"这类扩张前置条件
    if funnel.get("level") in ("NO_PAYMENT", "BELOW_THRESHOLD") or (
            funnel.get("level") == "NO_DATA" and not econ.get("orders")):
        return pack("fix_offer", "尚无付款证据，先改客户 / 痛点 / 信任证明 / Offer：%s" % funnel.get("reason"))
    if econ.get("level") in ("LOSS", "INCOMPLETE"):
        return pack("shrink", "已有付款但单位经济为负或数据不全（第六章 6.3）：%s" % econ.get("reason"))
    if conc is not None and conc > 0.5:
        return pack("reduce_concentration",
                    "单一客户收入占比 %.0f%%，超过 50%%（扩张前置条件）" % (conc * 100))
    if verdict == "KEEP" and econ.get("level") == "PASS" and cycles_paid >= 2:
        return pack("continue", "连续 %d 个周期有付款、交付可控且单位经济达标" % int(cycles_paid))
    if verdict == "KEEP" and econ.get("level") == "PASS":
        return pack("continue", "有付款且单位经济达标，但记录周期不足 2 轮（当前 %d）" % int(cycles_paid))
    return pack("fix_offer", "默认落到「先改 Offer 不扩张」：%s" % (funnel.get("reason") or "无付款证据"))


def judge_board(cfg, mods=None, modes_res=None):
    """第二层合并看板：9.1 通用 6 类 + 12.4 五槽位 + 记录格式 + 9.2 决策。

    两层是 AND：看板必须**回显第一层状态**，第一层未过时在顶部明示，不构成扩大投入依据。
    """
    mods = mods or load_modes()
    l2 = cfg.get("layer2") or {}
    assay = judge_assay(cfg)
    modes_res = modes_res if modes_res is not None else judge_modes(cfg, mods)
    layer1_ok = assay.get("level") == "READY_FOR_LAYER2"

    spec = mods["board_slots"]
    slots_in = l2.get("slots") or {}

    def collect(v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        return [x for x in v if not blank(x)]

    ns = collect(slots_in.get("north_star"))
    proc = collect(slots_in.get("process"))
    safe = collect(slots_in.get("safety"))
    slots = []
    for key, vals in (("north_star", ns), ("process", proc), ("safety", safe)):
        sp = spec[key]
        slots.append({
            "key": key, "label": sp["label"], "desc": sp["desc"],
            "required": sp["count"], "filled": vals,
            "ok": len(vals) >= sp["count"],
            "pool": ((modes_res.get("slot_pool") or {}).get(key) or []) if modes_res.get("ok") else [],
        })

    weekly = []
    for g in mods["board_general"]:
        v = (l2.get("metrics") or {}).get(g["key"])
        weekly.append({"key": g["key"], "name": g["name"], "metrics": g["metrics"],
                       "value": v, "filled": not blank(v)})

    autofill = {
        "我的 OPC 模式": (modes_res.get("mode") or {}).get("name"),
        "主要收入来源": l2.get("revenue_source"),
        "第一层五道闸门当前状态": "%s（%s）" % (assay.get("level"),
                                              ASSAY_LEVELS.get(assay.get("level"), "")),
    }
    # 五槽位已填时，记录格式里对应的 5 项自动回填，避免同一件事填两遍
    for fld, vals in (("北极星指标", ns), ("过程指标 1", proc[:1]), ("过程指标 2", proc[1:2]),
                      ("安全指标 1", safe[:1]), ("安全指标 2", safe[1:2])):
        if vals and not blank(vals[0]):
            autofill.setdefault(fld, vals[0])
    rec_in = l2.get("record") or {}
    record = []
    for f in mods["record_fields"]:
        val = rec_in.get(f)
        auto = False
        if blank(val) and not blank(autofill.get(f)):
            val, auto = autofill[f], True
        record.append({"field": f, "value": val, "auto": auto})

    return {
        "layer1": {
            "level": assay.get("level"),
            "level_text": assay.get("level_text"),
            "ok": layer1_ok,
            "reason": assay.get("reason"),
            "min_evidence": mods["layer1_min_evidence"],
            "gaps": assay.get("gaps") or [],
            "caps": assay.get("caps") or [],
            "may_enter_layer2": assay.get("may_enter_layer2"),
        },
        "gated": not layer1_ok,
        "gate_notice": (None if layer1_ok else
                        "第一层未通过（%s）。本看板只作为记录模板，不构成扩大投入或全职化的依据——"
                        "两层闸门必须同时通过。" % assay.get("level")),
        "mode": modes_res.get("mode"),
        "category": modes_res.get("category"),
        "mode_unresolved": not modes_res.get("ok"),
        "overlays": modes_res.get("overlays") or [],
        "slots": slots,
        "slots_complete": all(s["ok"] for s in slots),
        "weekly": weekly,
        "decision": decide_board(assay, modes_res, cfg, mods),
        "record": record,
        "record_complete": all(not blank(r["value"]) for r in record),
        "source": "第九章 9.1 / 9.2 · 第十二章 12.1 / 12.3 / 12.4",
    }


def layer2_payload(cfg, mods=None):
    """report / board / CLI 共用的第二层载荷。"""
    mods = mods or load_modes()
    m = judge_modes(cfg, mods)
    b = judge_board(cfg, mods, modes_res=m)
    return {"modes": m, "board": b}


# ══════════════════════════════════════════════════════════════════
# HTML 决策书（对外交付形态）
# ══════════════════════════════════════════════════════════════════

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#f4f4f8;color:#1a1a24;
 font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
 line-height:1.65;-webkit-font-smoothing:antialiased}
.wrap{max-width:920px;margin:0 auto;padding:0 20px 64px}
.cover{background:#6C5CE7;color:#fff;border-radius:0 0 20px 20px;padding:44px 40px 36px;margin-bottom:26px}
.cover .eyebrow{font-size:12px;letter-spacing:.18em;text-transform:uppercase;opacity:.72;margin:0 0 12px}
.cover h1{margin:0 0 14px;font-size:27px;line-height:1.35;font-weight:500}
.cover .meta{font-size:13px;opacity:.85}
.badge{display:inline-block;margin-top:18px;padding:7px 16px;border-radius:999px;
 background:rgba(255,255,255,.16);font-size:13px;border:1px solid rgba(255,255,255,.3)}
.card{background:#fff;border:1px solid #e6e6ef;border-radius:14px;padding:26px 28px;margin-bottom:18px}
.card h2{margin:0 0 4px;font-size:17px;font-weight:500;color:#1a1a24}
.card .src{font-size:12px;color:#9a9aad;margin:0 0 18px}
.card h3{margin:22px 0 10px;font-size:14px;font-weight:500;color:#4a4a5c}
h2 .num{display:inline-block;min-width:24px;height:24px;line-height:24px;text-align:center;
 background:#6C5CE7;color:#fff;border-radius:7px;font-size:12px;margin-right:10px;vertical-align:2px}
.verdict{border-radius:14px;padding:22px 26px;margin-bottom:18px;border:1px solid}
.verdict .lvl{font-size:20px;font-weight:500;margin:0 0 6px}
.verdict .txt{font-size:13.5px;margin:0}
.v-ready{background:#E1F5EE;border-color:#9FE1CB;color:#085041}
.v-cond{background:#FAEEDA;border-color:#FAC775;color:#633806}
.v-insuf{background:#FCEBEB;border-color:#F7C1C1;color:#791F1F}
.v-lock{background:#FCEBEB;border-color:#F09595;color:#501313}
.v-inc{background:#F1EFE8;border-color:#D3D1C7;color:#2C2C2A}
table{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid #eeedf3;vertical-align:top}
th{font-weight:500;color:#6b6b7b;font-size:12px;background:#fafafd}
td.n,th.n{text-align:right;white-space:nowrap}
tr:last-child td{border-bottom:none}
.kv{display:grid;grid-template-columns:180px 1fr;gap:8px 16px;font-size:13.5px;margin:12px 0}
.kv dt{color:#6b6b7b}
.kv dd{margin:0;font-weight:500}
.bar{height:8px;background:#eeedf3;border-radius:4px;overflow:hidden;min-width:80px}
.bar i{display:block;height:100%;border-radius:4px}
ul.clean{margin:10px 0;padding-left:0;list-style:none}
ul.clean li{position:relative;padding-left:20px;margin-bottom:9px;font-size:13.5px}
ul.clean li:before{content:"";position:absolute;left:4px;top:9px;width:6px;height:6px;
 border-radius:50%;background:#6C5CE7}
ul.ok li:before{background:#00B894}
ul.warn li:before{background:#e17055}
.tag{display:inline-block;padding:2px 9px;border-radius:6px;font-size:11.5px;margin-right:6px;
 background:#EEEDFE;color:#3C3489;white-space:nowrap}
.tag.red{background:#FCEBEB;color:#791F1F}
.tag.amber{background:#FAEEDA;color:#633806}
.tag.green{background:#E1F5EE;color:#085041}
.tag.gray{background:#F1EFE8;color:#4a4a5c}
.muted{color:#8a8a9c;font-size:12.5px}
.disclaimer{background:#fafafd;border:1px dashed #d8d8e4;border-radius:12px;padding:18px 20px;
 font-size:12.5px;color:#6b6b7b;margin-bottom:18px}
.disclaimer strong{color:#4a4a5c;font-weight:500;display:block;margin-bottom:6px}
footer{text-align:center;font-size:12px;color:#9a9aad;padding:26px 0 0;border-top:1px solid #e6e6ef;margin-top:26px}
.quote{border-left:3px solid #00B894;padding:4px 0 4px 14px;margin:12px 0;color:#4a4a5c;font-size:13px}
@media print{body{background:#fff}.card{break-inside:avoid;box-shadow:none}}
"""

VERDICT_CLASS = {
    "READY_FOR_LAYER2": "v-ready",
    "CONDITIONAL": "v-cond",
    "INSUFFICIENT": "v-insuf",
    "LOCKED": "v-lock",
    "INCOMPLETE": "v-inc",
}

MASKED = "—"
MASKED_NOTE = "已脱敏"


def _money(v, mask, unit=""):
    """对外交付时隐藏金额绝对值，只保留比例类指标（利润率、时薪相对目标的比例等）。"""
    if mask:
        return "%s（%s）" % (MASKED, MASKED_NOTE)
    if v is None:
        return "—"
    return ("%.0f" % v) + unit


def _pct(v):
    return "—" if v is None else "%.1f%%" % v


def _label(v, mask, idx):
    """订单/客户标签。对外交付时替换为序号，避免泄露真实客户名称或项目代号。"""
    if mask:
        return "订单 %d" % idx
    return v or ("订单 %d" % idx)


def _bar(pct, color):
    pct = max(0.0, min(100.0, pct or 0.0))
    return '<div class="bar"><i style="width:%.1f%%;background:%s"></i></div>' % (pct, color)


def _tag(text, kind=""):
    return '<span class="tag %s">%s</span>' % (kind, esc(text))


def render_report(cfg, mask=None):
    res = judge_assay(cfg)
    level = res["level"]
    gate1 = res["gate1"] or {}
    score = res["score"] or {}
    econ = res["econ"] or {}
    g3 = res.get("gate3") or {}
    g4 = res.get("gate4") or {}
    direct = g3.get("direct") or {}
    funnel = g3.get("funnel") or {}
    mso = g4.get("mso") or {}
    delivery = g4.get("delivery") or {}
    stopinfo = res.get("stop") or {}
    user = cfg.get("user") or {}

    audience = (user.get("audience") or "external").lower()
    external = audience != "internal"
    if mask is None:
        mask = external                      # 对外交付默认脱敏
    name = "OPC 创始人" if mask else (user.get("name") or "未署名")
    today_s = user.get("date") or today()
    signals = scan_signals(cfg.get("signal_texts"))

    parts = []
    parts.append('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    title = "%s 的 OPC 最小验证决策书" % name
    parts.append("<title>%s</title><style>%s</style></head><body>" % (esc(title), CSS))
    parts.append('<div class="wrap">')

    parts.append('<header class="cover">')
    parts.append('<p class="eyebrow">OPC 启动验证器 · 第一层自查报告</p>')
    parts.append("<h1>%s</h1>" % esc(title))
    meta = "生成日期 %s · 引擎 %s v%s" % (esc(today_s), ENGINE, VERSION)
    if mask:
        meta += " · 已脱敏"
    parts.append('<p class="meta">%s</p>' % meta)
    parts.append('<div class="badge">第一层结论：%s</div>' % esc(level))
    parts.append("</header>")

    parts.append('<div class="disclaimer"><strong>免责与使用边界</strong>%s</div>'
                 % esc(DISCLAIMER if external else DISCLAIMER_INTERNAL))

    parts.append('<div class="verdict %s">' % VERDICT_CLASS.get(level, "v-inc"))
    parts.append('<p class="lvl">%s</p>' % esc(ASSAY_LEVELS.get(level, level)))
    parts.append('<p class="txt">%s</p></div>' % esc(res.get("reason", "")))

    parts.append('<div class="card"><h2><span class="num">1</span>执行摘要</h2>')
    parts.append('<p class="src">综合第一章五道闸门、第三章适配度、第四至七章方向与验证、第十章退出的判据</p>')
    summ = []
    if gate1:
        lv = gate1.get("level")
        tag = {"CLEAR": ("未命中", "green"), "SOFT_ALERT": ("软风险超限", "amber"),
               "LOCKED": ("红线命中", "red"), "INCOMPLETE": ("未写完", "gray")}.get(lv, (lv, ""))
        summ.append(("硬红线与止损", "%s（硬红线 %d 条 · 软风险 %d 项）"
                     % (tag[0], gate1.get("redline_count", 0), gate1.get("soft_count", 0)), tag[1]))
    if score:
        summ.append(("适配度评分", "%s / 100（%s）" % (score.get("total"), score.get("band", "").split(" ")[0]),
                     "green" if score.get("total", 0) >= 80 else ("amber" if score.get("total", 0) >= 60 else "red")))
        if score.get("capped_count"):
            summ.append(("证据缺口", "%d 项无证据，已封顶 2 分" % score["capped_count"], "amber"))
    if econ:
        econ_tag = {"PASS": ("通过", "green"), "NO_DATA": ("无交易数据", "gray"),
                    "BELOW_TARGET": ("低于目标时薪", "amber"), "LOSS": ("亏损", "red"),
                    "INCOMPLETE": ("数据不全", "gray")}.get(econ.get("level"), (econ.get("level"), ""))
        summ.append(("单位经济", econ_tag[0], econ_tag[1]))
    g3t = {"OK": ("有合格方向", "green"), "NO_QUALIFIED": ("无合格方向", "red"),
           "NO_DATA": ("未评估", "gray")}.get(direct.get("level"), (direct.get("level"), "gray"))
    summ.append(("闸门 3 方向选择", "%s（推荐：%s）" % (g3t[0], direct.get("recommended") or "—"), g3t[1]))
    ft = {"PASS": ("门槛达标", "green"), "NO_PAYMENT": ("尚无付款", "red"),
          "BELOW_THRESHOLD": ("门槛未达", "amber"), "NO_DATA": ("未评估", "gray")}.get(funnel.get("level"),
                                                                                    (funnel.get("level"), "gray"))
    th = funnel.get("thresholds") or []
    summ.append(("闸门 3 预验证", "%s（访谈 %s · 愿进一步 %s · 付款 %s）" % (
        ft[0], th[0]["actual"] if th else "—", th[1]["actual"] if th else "—",
        th[2]["actual"] if th else "—"), ft[1]))
    mt = {"OK": ("可售", "green"), "INCOMPLETE": ("字段缺失", "amber"),
          "NOT_READY": ("未达可售门槛", "amber"), "NO_DATA": ("未评估", "gray")}.get(mso.get("level"),
                                                                                (mso.get("level"), "gray"))
    summ.append(("闸门 4 最小可售 Offer", "%s（标准 %s/%s · 字段 %s/%s）" % (
        mt[0], mso.get("pass_count", 0), mso.get("total_standards", 7),
        mso.get("field_count", len(MSO_FIELDS)) - len(mso.get("missing_fields") or []),
        mso.get("field_count", len(MSO_FIELDS))), mt[1]))
    if delivery.get("level") and delivery.get("level") != "NO_DATA":
        dt = ("通过", "green") if delivery["level"] == "PASS" else ("部分满足", "amber")
        summ.append(("闸门 4 首 3 个客户", "%s（%s/%s）" % (dt[0], delivery.get("pass_count"),
                                                       delivery.get("total_checks")), dt[1]))
    if stopinfo.get("verdict") and stopinfo["verdict"] != "NO_DATA":
        st = {"KEEP": ("继续", "green"), "ADJUST": ("调整后重试", "amber"),
              "PAUSE": ("暂停", "red"), "STOP": ("建议停止或转向", "red"),
              "EXIT": ("退出当前方向", "red"), "EXIT_NOW": ("立即停止全职投入", "red")}.get(
            stopinfo["verdict"], (stopinfo["verdict"], "gray"))
        summ.append(("止损监控", st[0], st[1]))
    parts.append('<dl class="kv">')
    for k, v, _c in summ:
        parts.append("<dt>%s</dt><dd>%s</dd>" % (esc(k), esc(v)))
    parts.append("</dl>")
    if res.get("caps"):
        parts.append("<h3>被压制的档位</h3><p class='muted'>下列阻断项把结论压低到当前档位：</p><ul class='clean warn'>")
        for c in res["caps"]:
            parts.append("<li>%s</li>" % esc(c))
        parts.append("</ul>")
    sg = res.get("scale_gate") or {}
    if sg.get("items"):
        parts.append("<h3>扩大投入闸门（第十一章 / 第十二章）</h3>")
        parts.append("<table><tr><th>条件</th><th class='n'>是否满足</th><th>依据</th></tr>")
        for i in sg["items"]:
            parts.append("<tr><td>%s</td><td class='n'>%s</td><td class='muted'>%s</td></tr>"
                         % (esc(i["label"]), _tag("满足", "green") if i["pass"] else _tag("未满足", "red"),
                            esc(i.get("detail") or "")))
        parts.append("</table>")
        if not sg.get("pass"):
            parts.append('<div class="quote">四项未同时满足，因此本报告不构成扩大投入或全职化的依据。'
                         "两层闸门必须同时通过，才适合讨论扩大投入。</div>")
    if res.get("gaps"):
        parts.append("<h3>需要关注的缺口</h3><ul class='clean warn'>")
        for g in res["gaps"]:
            parts.append("<li>%s</li>" % esc(g))
        parts.append("</ul>")
    parts.append("</div>")

    if gate1:
        parts.append('<div class="card"><h2><span class="num">2</span>闸门 1 · 生存与合规</h2>')
        parts.append('<p class="src">出处：第二章 2.1 硬红线 · 2.2 软风险项 · 2.3 止损三数字</p>')
        parts.append("<h3>硬红线（命中即锁定，评分不可覆盖）</h3>")
        if gate1.get("redline_hits"):
            parts.append("<table><tr><th>命中的硬红线</th></tr>")
            for h in gate1["redline_hits"]:
                parts.append("<tr><td>%s %s</td></tr>" % (_tag("命中", "red"), esc(h["text"])))
            parts.append("</table>")
        else:
            parts.append("<p class='muted'>6 条硬红线均未命中。</p>")
        parts.append("<h3>软风险项（不否决，但须写入修复计划）</h3>")
        if gate1.get("soft_hits"):
            parts.append("<ul class='clean warn'>")
            for h in gate1["soft_hits"]:
                parts.append("<li>%s</li>" % esc(h["text"]))
            parts.append("</ul>")
            if gate1.get("soft_overlay_triggered"):
                parts.append('<div class="quote">软风险命中 %d 项（≥3）：即使适配度总分达到 80 分，'
                             "也只能副业验证。</div>" % gate1["soft_count"])
        else:
            parts.append("<p class='muted'>未命中软风险项。</p>")
        parts.append("<h3>止损三数字</h3>")
        stop = gate1.get("stop_loss") or {}
        parts.append('<dl class="kv">')
        for label, key, default in (("最大现金投入", "max_cash", "未写"),
                                    ("最长试跑时间", "max_weeks", "未写"),
                                    ("最低继续结果", "min_result", "未写")):
            val = stop.get(key)
            if mask and val:
                val = "%s（%s）" % (MASKED, MASKED_NOTE)
            parts.append("<dt>%s</dt><dd>%s</dd>" % (label, esc(val or default)))
        parts.append("</dl>")
        if gate1.get("cushion_months") is not None and not mask:
            parts.append("<p class='muted'>安全垫 = 可动用现金 ÷ 每月最低必要支出 = %.1f 个月（目标 %s 个月）</p>"
                         % (gate1["cushion_months"], gate1.get("cushion_target_months") or "未设定"))
        parts.append("</div>")

    if score:
        parts.append('<div class="card"><h2><span class="num">3</span>闸门 2 · OPC 适配度评分</h2>')
        parts.append('<p class="src">出处：第三章 3.1 评分规则 · 3.2 评分表 · 3.3 结果解释</p>')
        parts.append("<h3>四个维度</h3><table><tr><th>维度</th><th>得分</th><th>占比</th><th></th></tr>")
        for d in score["dimensions"]:
            col = "#00B894" if d["pct"] >= 80 else ("#BA7517" if d["pct"] >= 50 else "#A32D2D")
            parts.append("<tr><td>%s</td><td class='n'>%.1f / %d</td><td class='n'>%.0f%%</td>"
                         "<td>%s</td></tr>" % (esc(d["dimension"]), d["gained"], d["max"], d["pct"], _bar(d["pct"], col)))
        parts.append("</table>")
        parts.append("<h3>逐项明细（%s / 100）</h3>" % score["total"])
        parts.append("<table><tr><th>评估项</th><th class='n'>权重</th><th class='n'>得分</th>"
                     "<th class='n'>计入</th><th>证据</th></tr>")
        for r in score["rows"]:
            flag = ""
            if "capped" in r["flags"]:
                flag = _tag("封顶", "amber")
            elif "missing" in r["flags"]:
                flag = _tag("未评估", "gray")
            parts.append("<tr><td>%s<div class='muted'>%s</div></td><td class='n'>%d</td><td class='n'>%s</td>"
                         "<td class='n'>%.2f %s</td><td>%s</td></tr>"
                         % (esc(r["item"]), esc(r["dimension"]), r["weight"],
                            "-" if r["raw_score"] is None else esc(r["raw_score"]),
                            r["gained"], flag, esc(r["evidence"]) or "<span class='muted'>无</span>"))
        parts.append("</table>")
        parts.append("<h3>评分锚定（第三章 3.1）</h3>")
        parts.append("<table><tr><th>分</th><th>含义</th></tr>")
        for lo, label in SCORE_LEVELS:
            parts.append("<tr><td class='n'>%s</td><td>%s</td></tr>" % (
                ("%d" % lo) if lo in (0, 5) else ("%d–%d" % (lo, lo + 1)), esc(label)))
        parts.append("</table>")
        parts.append("<p class='muted'>只有「我觉得我可以」而没有事实记录时，最高只能打 2 分——"
                     "这一条由引擎强制执行，不依赖自觉。</p>")
        parts.append("</div>")

    parts.append('<div class="card"><h2><span class="num">4</span>闸门 3 · 问题与客户</h2>')
    parts.append('<p class="src">出处：第四章 4.1 方向生成 · 4.2 方向评分卡 · 4.3 筛选原则 · 第五章 5.2/5.3</p>')
    parts.append("<h3>候选方向（第四章 4.2 评分卡，权重合计 100%）</h3>")
    if direct.get("directions"):
        parts.append("<table><tr><th>方向</th><th>证据来源</th><th class='n'>加权总分</th><th>硬约束</th></tr>")
        for d in direct["directions"]:
            hard = _tag("合格", "green") if d["qualified"] else (
                _tag("不合规", "red") if d["floor_violations"] else _tag("评分不全", "gray"))
            parts.append("<tr><td>%s%s</td><td class='muted'>%s</td><td class='n'>%s</td><td>%s</td></tr>"
                         % (esc(d["name"]),
                            "<div class='muted'>%s</div>" % esc(" · ".join(d["label_flags"])) if d["label_flags"] else "",
                            esc(d.get("basis_text") or d.get("basis") or "未标注"),
                            d["total"], hard))
        parts.append("</table>")
        for d in direct["directions"]:
            if d["floor_violations"]:
                parts.append("<p class='muted'>「%s」违反三项硬约束：%s</p>" % (
                    esc(d["name"]), esc("、".join("%s %s 分" % (v["item"], v["score"])
                                               for v in d["floor_violations"]))))
        if direct.get("recommended"):
            parts.append('<div class="quote">优先推进：%s（总分最高且无硬约束违规）</div>'
                         % esc(direct["recommended"]))
    else:
        parts.append("<p class='muted'>未提供候选方向。方向必须从下面四类证据出发生成，"
                     "不得从模式目录反推——那是把流程降级成赛道挑选器。</p>")
    parts.append("<h3>方向生成的四类证据（第四章 4.1）</h3><ul class='clean'>")
    for b in direct.get("bases_reference") or []:
        parts.append("<li>%s</li>" % esc(b["text"]))
    parts.append("</ul>")
    parts.append("<h3>三个筛选原则（第四章 4.3）</h3><ul class='clean'>")
    for p in direct.get("screening_principles") or []:
        parts.append("<li>%s</li>" % esc(p))
    parts.append("</ul>")

    parts.append("<h3>预验证门槛（第五章 5.2）</h3>")
    if funnel.get("thresholds"):
        parts.append("<table><tr><th>门槛</th><th>要求</th><th class='n'>实际</th><th class='n'>结果</th></tr>")
        for t in funnel["thresholds"]:
            parts.append("<tr><td>%s</td><td class='muted'>%s</td><td class='n'>%s</td><td class='n'>%s</td></tr>"
                         % (esc(t["label"]), esc(t["required"]), t["actual"],
                            _tag("达标", "green") if t["pass"] else _tag("未达", "red")))
        parts.append("</table>")
    else:
        parts.append("<p class='muted'>未记录访谈到成交的门槛数据。</p>")
    if funnel.get("stages") and any(s["value"] is not None for s in funnel["stages"]):
        parts.append("<h3>访谈到成交的漏斗（第五章 5.3）</h3>")
        parts.append("<table><tr><th>环节</th><th>记录指标</th><th class='n'>数量</th>"
                     "<th class='n'>环比转化</th></tr>")
        for s in funnel["stages"]:
            mark = ""
            if funnel.get("bottleneck") and funnel["bottleneck"]["key"] == s["key"]:
                mark = _tag("瓶颈", "amber")
            parts.append("<tr><td>%s %s</td><td class='muted'>%s</td><td class='n'>%s</td>"
                         "<td class='n'>%s</td></tr>"
                         % (esc(s["label"]), mark, esc(s["metric"]),
                            "—" if s["value"] is None else s["value"],
                            "—" if s["conversion_pct"] is None else "%.1f%%" % s["conversion_pct"]))
        parts.append("</table>")
    if funnel.get("level") == "NO_PAYMENT":
        parts.append('<div class="quote">尚无真实付款。无法收款时应归因到具体环节，'
                     "而不是直接得出「市场不存在」：%s</div>" % esc(" · ".join(LOSS_REASONS)))
    parts.append("</div>")

    parts.append('<div class="card"><h2><span class="num">5</span>闸门 4 · 付费与交付</h2>')
    parts.append('<p class="src">出处：第六章 6.1 一页纸启动书 · 6.2 MSO 最低标准 · 第七章 7.2 首 3 个客户</p>')
    parts.append("<h3>MSO 最低标准（第六章 6.2）</h3>")
    if mso.get("standards"):
        parts.append("<table><tr><th class='n'>#</th><th>标准</th><th class='n'>结果</th><th>说明</th></tr>")
        for s in mso["standards"]:
            parts.append("<tr><td class='n'>%d</td><td>%s</td><td class='n'>%s</td><td class='muted'>%s</td></tr>"
                         % (s["no"], esc(s["text"]),
                            _tag("满足", "green") if s["pass"] else _tag("未满足", "red"),
                            esc(s["detail"] or "")))
        parts.append("</table>")
        parts.append("<p class='muted'>通过 %d / %d。</p>" % (mso["pass_count"], mso["total_standards"]))
    else:
        parts.append("<p class='muted'>未提供 MSO。闸门 4 需要一份范围明确、周期清楚、必须收费的最小可售 Offer。</p>")
    if mso.get("missing_fields"):
        parts.append("<h3>一页纸启动书缺失字段（第六章 6.1）</h3><ul class='clean warn'>")
        for f in mso["missing_fields"]:
            parts.append("<li>%s</li>" % esc(f))
        parts.append("</ul>")
    if delivery.get("checks"):
        parts.append("<h3>首 3 个客户的验证标准（第七章 7.2）</h3>")
        parts.append("<table><tr><th>条件</th><th class='n'>满足</th></tr>")
        for c in delivery["checks"]:
            parts.append("<tr><td>%s</td><td class='n'>%s</td></tr>"
                         % (esc(c["text"]), _tag("是", "green") if c["pass"] else _tag("否", "red")))
        parts.append("</table>")
    parts.append("</div>")

    if econ:
        parts.append('<div class="card"><h2><span class="num">6</span>闸门 5 · 单位经济核算</h2>')
        parts.append('<p class="src">出处：第六章 6.3 单位经济模型 · 第九章 9.1 经营看板</p>')
        if econ["level"] == "NO_DATA":
            parts.append("<p class='muted'>尚无交易数据，单位经济待有真实订单后回填。</p>")
        else:
            parts.append("<table><tr><th>订单</th><th class='n'>收入</th><th class='n'>直接成本</th>"
                         "<th class='n'>工时</th><th class='n'>贡献利润</th><th class='n'>利润率</th>"
                         "<th class='n'>有效时薪</th></tr>")
            for idx, o in enumerate(econ["orders"], 1):
                parts.append("<tr><td>%s</td><td class='n'>%s</td><td class='n'>%s</td><td class='n'>%s</td>"
                             "<td class='n'>%s</td><td class='n'>%s</td><td class='n'>%s</td></tr>"
                             % (esc(_label(o["order"], mask, idx)),
                                _money(o["revenue"], mask),
                                _money(o["direct_cost"], mask),
                                "-" if o["hours"] is None else "%.1f" % o["hours"],
                                _money(o["contribution"], mask),
                                _pct(o["margin"]),
                                _money(o["effective_hourly"], mask)))
            parts.append("</table>")
            if mask and econ["orders"]:
                parts.append("<p class='muted'>对外交付版已隐藏金额绝对值，只保留利润率等比例指标；"
                             "完整数字见工作目录中的状态档案。</p>")
            a = econ["aggregate"]
            parts.append('<dl class="kv">'
                         "<dt>合计收入 / 直接成本</dt><dd>%s / %s</dd>"
                         "<dt>总投入工时</dt><dd>%.1f 小时</dd>"
                         "<dt>合计贡献利润</dt><dd>%s</dd>"
                         "<dt>综合贡献利润率</dt><dd>%s</dd>"
                         "<dt>综合有效时薪</dt><dd>%s</dd>"
                         "<dt>目标时薪</dt><dd>%s</dd>"
                         "<dt>获客回本周期</dt><dd>%s</dd></dl>"
                         % (_money(a["revenue"], mask), _money(a["direct_cost"], mask), a["hours"],
                            _money(a["contribution"], mask),
                            _pct(a["contribution_margin"]),
                            _money(a["effective_hourly"], mask),
                            _money(econ.get("target_hourly_rate"), mask),
                            "—" if econ.get("payback_periods") is None
                            else "%.2f 个月（获客成本 %s ÷ 单客月贡献利润 %s）"
                                 % (econ["payback_periods"], _money(econ.get("cac"), mask),
                                    _money(cfg.get("econ", {}).get("monthly_contribution_per_customer"), mask))))
            parts.append("<p class='muted'>%s</p>" % esc(econ["reason"]))
            if econ.get("incomplete"):
                parts.append("<p class='muted'>下列订单因缺少收入或工时被排除在计算之外：%s</p>"
                             % esc("、".join(_label(x["order"], mask, i)
                                             for i, x in enumerate(econ["incomplete"], 1))))
        parts.append("</div>")

    parts.append('<div class="card"><h2><span class="num">7</span>信号真实性检查</h2>')
    parts.append('<p class="src">出处：第一章信号分级 · 第十二章 12.5 五类伪验证信号</p>')
    parts.append("<h3>信号强度分级（只有第 4 级算强验证）</h3>")
    parts.append("<table><tr><th class='n'>级</th><th>名称</th><th>形态</th>"
                 "<th class='n'>算强验证</th><th>追问</th></tr>")
    for lv, label, form, question, strong, _kws in SIGNAL_LEVELS:
        parts.append("<tr><td class='n'>%d</td><td>%s</td><td>%s</td><td class='n'>%s</td>"
                     "<td class='muted'>%s</td></tr>"
                     % (lv, _tag(label, "green" if strong else "gray"), esc(form),
                        "是" if strong else "否", esc(question)))
    parts.append("</table>")
    parts.append("<h3>本次会话记录到的表述</h3>")
    if signals["scanned"] == 0:
        parts.append("<p class='muted'>未提供会话中的进展表述，无法做伪验证信号检查。</p>")
    else:
        if signals["pseudo_hits"]:
            parts.append("<table><tr><th>命中的表面信号</th><th>为什么不足</th><th>应替换为</th></tr>")
            seen = set()
            for h in signals["pseudo_hits"]:
                if h["key"] in seen:
                    continue
                seen.add(h["key"])
                parts.append("<tr><td>%s</td><td class='muted'>%s</td><td>%s</td></tr>"
                             % (_tag(h["surface"], "red"), esc(h["why"]), esc(h["replace_with"])))
            parts.append("</table>")
            parts.append("<p class='muted'>以上表述不得作为已验证证据，必须换用右列指标重新计数。</p>")
        else:
            parts.append("<p class='muted'>未命中五类伪验证信号。</p>")
        lv = signals.get("strongest_signal_level")
        if lv is None:
            parts.append("<p class='muted'>未识别到任何信号分级表述。</p>")
        else:
            label = dict((x[0], x[1]) for x in SIGNAL_LEVELS)[lv]
            strong = "是" if signals.get("strongest_is_strong") else "否"
            parts.append("<p>本次会话最强信号：%s %s（算强验证：%s）</p>"
                         % (_tag(label, "green" if signals.get("strongest_is_strong") else "amber"),
                            "", strong))
    parts.append("<div class='quote'>%s</div>" % esc(res["evidence_rule"]))
    parts.append("</div>")

    if stopinfo.get("verdict") and stopinfo["verdict"] != "NO_DATA":
        parts.append('<div class="card"><h2><span class="num">8</span>止损与退出监控</h2>')
        parts.append('<p class="src">出处：第二章 2.3 止损三数字 · 第七章 7.3 调整顺序 · 第十章退出与替代路径</p>')
        st = {"KEEP": ("继续", "green"), "ADJUST": ("调整后重试", "amber"),
              "PAUSE": ("暂停", "red"), "STOP": ("建议停止或转向", "red"),
              "EXIT": ("退出当前方向", "red"), "EXIT_NOW": ("立即停止全职投入", "red")}.get(
            stopinfo["verdict"], (stopinfo["verdict"], "gray"))
        parts.append("<p>当前判定：%s —— %s</p>" % (_tag(st[0], st[1]), esc(stopinfo["reason"])))
        parts.append('<dl class="kv">'
                     "<dt>现金投入 / 上限</dt><dd>%s / %s</dd>"
                     "<dt>已试跑时间 / 上限</dt><dd>%s / %s</dd>"
                     "<dt>已用验证轮次</dt><dd>%d / %d 轮（每轮约 %d 天，每轮最多改 %d 个变量）</dd></dl>"
                     % (_money(stopinfo.get("cash_spent"), mask), _money(stopinfo.get("cash_limit"), mask),
                        "—" if stopinfo.get("weeks_elapsed") is None else "%.1f 周" % stopinfo["weeks_elapsed"],
                        "—" if stopinfo.get("weeks_limit") is None else "%.1f 周" % stopinfo["weeks_limit"],
                        stopinfo.get("rounds_used", 0), stopinfo.get("rounds_limit", MAX_ROUNDS),
                        stopinfo.get("round_days", ROUND_DAYS), stopinfo.get("max_vars_per_round", 2)))
        if stopinfo.get("rounds_over_vars"):
            parts.append("<p class='muted'>第 %s 轮同时改动的变量超过 %d 个，无法定位是哪一项起了作用。</p>"
                         % (esc("、".join(str(x) for x in stopinfo["rounds_over_vars"])),
                            stopinfo.get("max_vars_per_round", 2)))
        hits = [x for x in stopinfo.get("immediate", []) if x["hit"]]
        if hits:
            parts.append("<h3>已命中「立即停止全职投入」条件（第十章 10.1）</h3><ul class='clean warn'>")
            for x in hits:
                parts.append("<li>%s</li>" % esc(x["text"]))
            parts.append("</ul>")
        shits = [x for x in stopinfo.get("suggest", []) if x["hit"]]
        parts.append("<h3>「建议停止或转向」条件（第十章 10.2，命中任意两项即建议停止）</h3>")
        if shits:
            parts.append("<ul class='clean warn'>")
            for x in shits:
                parts.append("<li>%s</li>" % esc(x["text"]))
            parts.append("</ul>")
            parts.append("<p class='muted'>已命中 %d 项。</p>" % len(shits))
        else:
            parts.append("<p class='muted'>未命中。</p>")
        parts.append("<h3>失败时的调整顺序（第七章 7.3）</h3><ul class='clean'>")
        for i, step in enumerate(stopinfo.get("adjust_order") or [], 1):
            parts.append("<li>%d. %s</li>" % (i, esc(step)))
        parts.append("</ul>")
        parts.append("<h3>替代路径（第十章 10.3）</h3>")
        parts.append("<table><tr><th>路径</th><th>适合情况</th></tr>")
        for p in stopinfo.get("alt_paths") or []:
            parts.append("<tr><td>%s</td><td class='muted'>%s</td></tr>" % (esc(p["path"]), esc(p["fits"])))
        parts.append("</table>")
        parts.append("<p class='muted'>退出不是失败。应保留一份复盘：测试过的客户、实际报价、"
                     "付款证据、拒绝原因、交付工时、单位经济、合规问题和下一次不再重复的假设。</p>")
        parts.append("</div>")

    if res.get("repairs"):
        parts.append('<div class="card"><h2><span class="num">9</span>短板修复清单</h2>')
        parts.append('<p class="src">出处：第三章 3.4 短板修复计划（动作 / 频率 / 证据 / 截止时间）</p>')
        parts.append("<table><tr><th>短板</th><th>修复动作</th><th>期限</th><th>达标证据</th></tr>")
        for r in res["repairs"]:
            parts.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                         % (esc(r["shortfall"]), esc(r["action"]), esc(r["term"]), esc(r["proof"])))
        parts.append("</table>")
        parts.append("<p class='muted'>修复的最低目标：至少 1 个真实付费客户、一次完整回款、"
                     "一个可重复交付流程、一个可持续触达渠道，以及一份风险清单。</p>")
        parts.append("</div>")

    parts.append('<div class="card"><h2><span class="num">10</span>下一步</h2>')
    parts.append('<p class="src">按第五道闸门之前的推进顺序排列</p>')
    parts.append("<ul class='clean ok'>")
    for n in res.get("next_actions") or []:
        parts.append("<li>%s</li>" % esc(n))
    parts.append("</ul>")
    parts.append("<div class='quote'>只有「真实付款 + 可控交付 + 单位经济为正 + 风险可控」同时出现时，"
                 "才把方向视为初步验证成功。两层闸门必须同时通过，才适合扩大投入或考虑全职化"
                 "（第十一章 / 第十二章）。</div>")
    parts.append("</div>")

    if cfg.get("layer2"):
        for num, title, src, frag in _l2_sections(layer2_payload(cfg), mask, start_num=11):
            parts.append('<div class="card"><h2><span class="num">%s</span>%s</h2>'
                         % (num, esc(title)))
            if src:
                parts.append('<p class="src">%s</p>' % esc(src))
            parts.extend(frag)
            parts.append("</div>")

    foot = "本报告基于《OPC 单人创业自查与最小验证流程》生成"
    if not external:
        foot += " · 判据出处见 references/方法论全文.md"
    foot += "<br>由 %s v%s 判据引擎产出 · %s" % (ENGINE, VERSION, esc(today()))
    parts.append("<footer>%s</footer>" % foot)
    parts.append("</div></body></html>")
    return "".join(parts)


# ══════════════════════════════════════════════════════════════════
# 第二层 HTML 段落（决策书附录 + 独立看板共用）
# ══════════════════════════════════════════════════════════════════

def _l2_sections(payload, mask, start_num=11):
    """返回 [(编号, 标题, 出处, [html...]), ...]，供 report 附录与 board 独立文档复用。"""
    m = payload["modes"]
    b = payload["board"]
    out = []

    # ── §11 模式与专属指标 ──────────────────────────────────────────
    frag = []
    if m["level"] == "BLOCKED_ORDER":
        frag.append('<div class="verdict v-lock"><p class="lvl">第二层被顺序闸门拒绝</p>'
                    '<p class="txt">%s</p></div>' % esc(m["reason"]))
        frag.append("<p class='muted'>%s</p>" % esc(m["detail"]))
        frag.append('<dl class="kv"><dt>前置条件</dt><dd>%s</dd><dt>下一步</dt><dd>%s</dd>'
                    '<dt>出处</dt><dd>%s</dd></dl>'
                    % (esc(m["required"]), esc(m["next_action"]), esc(m["source"])))
        frag.append("<h3>八类模式地图（仅供了解范围，不能反用于选题）</h3>")
        frag.append("<table><tr><th class='n'>类</th><th>名称</th><th class='n'>模式数</th></tr>")
        for c in m.get("categories_overview", []):
            frag.append("<tr><td class='n'>%s</td><td>%s</td><td class='n'>%d</td></tr>"
                        % (esc(c["key"]), esc(c["name"]), c["mode_count"]))
        frag.append("</table>")
    elif not m["ok"]:
        frag.append('<div class="verdict v-inc"><p class="lvl">无法归类</p>'
                    '<p class="txt">%s</p></div>' % esc(m["reason"]))
        frag.append("<p class='muted'>%s</p>" % esc(m["next_action"]))
    else:
        frag.append('<dl class="kv">'
                    '<dt>第一模式</dt><dd>%s <span class="tag">%s</span></dd>'
                    '<dt>模式大类</dt><dd>%s · %s</dd>'
                    '<dt>对应方向</dt><dd>%s</dd>'
                    '<dt>主要收入来源</dt><dd>%s</dd>'
                    '<dt>主要风险来源</dt><dd>%s</dd>'
                    '<dt>匹配依据</dt><dd>%s</dd></dl>'
                    % (esc(m["mode"]["name"]), esc(m["mode"]["key"]),
                       esc(m["category"]["key"]), esc(m["category"]["name"]),
                       esc(m["direction"]), esc(m.get("revenue_source") or "—"),
                       esc(m.get("risk_source") or "—"),
                       esc("显式指定" if m["matched_by"] == "explicit"
                           else "关键词命中「%s」" % (m.get("keyword") or "—"))))
        if m["mode"].get("note"):
            frag.append("<div class='quote'>%s</div>" % esc(m["mode"]["note"]))
        if m["mode"].get("forms"):
            frag.append("<p class='muted'>典型形式：%s</p>" % esc(m["mode"]["forms"]))
        if m.get("ambiguous"):
            frag.append("<div class='quote'>匹配结果不唯一，前两名得分接近，请与用户确认后再锁指标。"
                        "候选：%s</div>"
                        % esc("；".join("%s（%s，%s 分）" % (c["name"], c["key"], c["score"])
                                        for c in m["candidates"])))
        if m.get("warning"):
            frag.append('<p class="muted">%s</p>' % esc(m["warning"]))

        pk = m["pack"]
        frag.append("<h3>该模式的专属指标（第十二章 12.3 · <span class='tag'>%s</span>%s）</h3>"
                    % (esc(pk["key"]), esc(pk["name"])))
        frag.append("<table><tr><th>分组</th><th>内容</th></tr>"
                    "<tr><td>核心验证信号</td><td>%s</td></tr>"
                    "<tr><td>必看经营指标</td><td>%s</td></tr>"
                    "<tr><td>单位经济 / 持续性</td><td>%s</td></tr>"
                    "<tr><td>主要专属风险</td><td>%s</td></tr></table>"
                    % (esc(pk["signal"]), esc(pk["ops"]), esc(pk["econ"]), esc(pk["risk"])))
        if m.get("overlays"):
            frag.append("<h3>叠加模式（只取指标，不改第一模式）</h3>")
            frag.append("<table><tr><th>指标包</th><th>核心验证信号</th><th>单位经济 / 持续性</th></tr>")
            for o in m["overlays"]:
                frag.append("<tr><td>%s</td><td class='muted'>%s</td><td class='muted'>%s</td></tr>"
                            % (esc(o["name"]), esc(o["pack"]["signal"]), esc(o["pack"]["econ"])))
            frag.append("</table>")
        frag.append("<p class='muted'>%s</p>" % esc(m["rule"]))
    out.append((start_num, "第二层 · 模式与专属指标", m.get("source", ""), frag))

    # ── §12 合并看板 ───────────────────────────────────────────────
    frag = []
    if b.get("gated"):
        frag.append('<div class="verdict v-insuf"><p class="lvl">第一层未通过 · 本看板不构成投入依据</p>'
                    '<p class="txt">%s</p></div>' % esc(b["gate_notice"]))
    l1 = b["layer1"]
    frag.append('<dl class="kv"><dt>第一层结论</dt><dd>%s <span class="tag %s">%s</span></dd>'
                '<dt>是否可进入第二层</dt><dd>%s</dd></dl>'
                % (esc(l1["level"]), "green" if l1["ok"] else "red",
                   esc(ASSAY_LEVELS.get(l1["level"], "")),
                   "是" if l1.get("may_enter_layer2") else "否"))
    frag.append("<p class='muted'>%s</p>" % esc(l1.get("reason") or ""))
    if l1.get("caps"):
        frag.append("<ul class='clean warn'>")
        for c in l1["caps"]:
            frag.append("<li>%s</li>" % esc(c))
        frag.append("</ul>")

    frag.append("<h3>五槽位（第十二章 12.4：北极星 1 + 过程 2 + 安全 2）</h3>")
    frag.append("<table><tr><th>槽位</th><th class='n'>应填</th><th>已填</th><th class='n'>状态</th></tr>")
    for s in b["slots"]:
        filled = "、".join(str(x) for x in s["filled"]) if s["filled"] else "—"
        frag.append("<tr><td>%s</td><td class='n'>%d</td><td>%s</td><td class='n'>%s</td></tr>"
                    % (esc(s["label"]), s["required"], esc(filled),
                       _tag("齐备", "green") if s["ok"] else _tag("待填", "amber")))
    frag.append("</table>")
    for s in b["slots"]:
        if s["ok"]:
            continue
        if s["pool"]:
            frag.append("<p class='muted'>%s 候选池：%s</p>"
                        % (esc(s["label"]), esc("、".join(str(x) for x in s["pool"][:8]))))
        else:
            frag.append("<p class='muted'>%s：%s</p>" % (esc(s["label"]), esc(s["desc"])))

    frag.append("<h3>每周经营看板（第九章 9.1，通用 6 类）</h3>")
    frag.append("<table><tr><th>类别</th><th>该记什么</th><th>本周期</th><th class='n'>状态</th></tr>")
    for w in b["weekly"]:
        frag.append("<tr><td>%s</td><td class='muted'>%s</td><td>%s</td><td class='n'>%s</td></tr>"
                    % (esc(w["name"]), esc(w["metrics"]),
                       esc(w["value"]) if w["filled"] else "—",
                       _tag("已填", "green") if w["filled"] else _tag("待填", "gray")))
    frag.append("</table>")

    d = b["decision"]
    frag.append("<h3>继续 / 调整 / 暂停（第九章 9.2 决策表）</h3>")
    frag.append('<dl class="kv"><dt>命中条件</dt><dd>%s</dd><dt>判定</dt><dd>%s</dd></dl>'
                % (esc(d["when"]), esc(d["decision"])))
    frag.append("<p class='muted'>依据：%s · 出处：%s</p>" % (esc(d["basis"]), esc(d["source"])))

    frag.append("<h3>记录格式（第十二章 12.4）</h3>")
    frag.append("<table><tr><th>字段</th><th>值</th></tr>")
    for r in b["record"]:
        val = esc(r["value"]) if not blank(r["value"]) else "<span class='muted'>待填</span>"
        if r.get("auto") and not blank(r["value"]):
            val += " <span class='tag gray'>自动</span>"
        frag.append("<tr><td>%s</td><td>%s</td></tr>" % (esc(r["field"]), val))
    frag.append("</table>")
    frag.append("<div class='quote'>第一层回答「这项尝试是否安全、合规、可持续地运行」；"
                "第二层回答「这种具体模式是否出现了该模式应有的真实增长信号」。"
                "只有两层同时通过，才适合扩大投入或考虑全职化（第十二章末）。</div>")
    out.append((start_num + 1, "第二层 · 合并看板", b.get("source", ""), frag))
    return out


def render_board(cfg, mask=None):
    """独立的第二层合并看板 HTML（决策书只带附录时用 `report`）。"""
    user = cfg.get("user") or {}
    external = (user.get("audience") or "external").lower() != "internal"
    if mask is None:
        mask = external
    name = "OPC 创始人" if mask else (user.get("name") or "未署名")
    date_s = user.get("date") or today()
    payload = layer2_payload(cfg)
    sections = _l2_sections(payload, mask, start_num=1)

    parts = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>%s 的 OPC 第二层合并看板</title><style>%s</style></head><body>"
             % (esc(name), CSS),
             '<div class="wrap">',
             '<header class="cover">',
             '<p class="eyebrow">OPC 启动验证器 · 第二层模式指标与合并看板</p>',
             "<h1>%s 的 OPC 第二层合并看板</h1>" % esc(name),
             '<p class="meta">生成日期 %s · 引擎 %s v%s%s</p>'
             % (esc(date_s), ENGINE, VERSION, " · 已脱敏" if mask else ""),
             '<div class="badge">第一层结论：%s</div>' % esc(payload["board"]["layer1"]["level"]),
             "</header>",
             '<div class="disclaimer"><strong>免责与使用边界</strong>%s</div>'
             % esc(DISCLAIMER if external else DISCLAIMER_INTERNAL)]
    for num, title, src, frag in sections:
        parts.append('<div class="card"><h2><span class="num">%s</span>%s</h2>' % (num, esc(title)))
        if src:
            parts.append('<p class="src">%s</p>' % esc(src))
        parts.extend(frag)
        parts.append("</div>")

    foot = "本看板基于《OPC 单人创业自查与最小验证流程》第九章 / 第十二章生成"
    if not external:
        foot += " · 判据出处见 references/方法论全文.md"
    foot += "<br>由 %s v%s 判据引擎产出 · %s" % (ENGINE, VERSION, esc(today()))
    parts.append("<footer>%s</footer>" % foot)
    parts.append("</div></body></html>")
    return "".join(parts)


# ══════════════════════════════════════════════════════════════════
# 输入模板
# ══════════════════════════════════════════════════════════════════

def template():
    t = {
        "user": {"name": "", "date": today(), "audience": "external"},
        "signal_texts": [],
        "gate1": {
            "safety_cushion": {"cash": None, "monthly_essential": None, "target_months": None},
            "redlines": {k: False for k, _ in HARD_REDLINES},
            "soft_risks": [],
            "stop_loss": {"max_cash": "", "max_weeks": "", "min_result": ""},
        },
        "score": {"items": {k: {"score": None, "evidence": ""} for k, _d, _t2, _w in SCORE_ITEMS}},
        "gate3": {
            "directions": [{"name": "", "basis": "past_results",
                            "scores": {k: None for k, _l, _w, _q in DIRECTION_ITEMS},
                            "notes": ""}],
            "funnel": {"targets": None, "reached": None, "interviews": None, "advanced": None,
                       "proposals": None, "quotes": None, "payers": None, "payments": None,
                       "deliveries": None, "results": None,
                       "loss_reasons": {k: 0 for k in LOSS_REASONS}},
        },
        "gate4": {
            "mso": {k: "" for k, _l in MSO_FIELDS},
            "delivery": {"paying_customers": None, "scope_controlled": None, "econ_positive": None,
                         "feedback": "", "follow_ons": None, "compliant": None, "still_willing": None},
        },
        "econ": {
            "target_hourly_rate": None,
            "orders": [{"customer": "", "revenue": None, "direct_cost": None, "hours": None}],
            "cac": None,
            "monthly_contribution_per_customer": None,
        },
        "tracking": {
            "cash_spent": None, "cash_limit": None, "weeks_elapsed": None, "weeks_limit": None,
            "rounds": [{"round": 1, "changed_vars": [], "payments": 0}],
            "immediate": {k: False for k, _ in STOP_IMMEDIATE},
            "suggest": {k: False for k, _ in STOP_SUGGEST},
        },
        "state": {"path": ""},
        "layer2": {
            "direction": "",
            "description": "",
            "revenue_source": "",
            "risk_source": "",
            "mode_key": "",
            "overlays": [],
            "slots": {"north_star": "", "process": ["", ""], "safety": ["", ""]},
            "metrics": {g["key"]: "" for g in load_modes()["board_general"]},
            "concentration": None,
            "cycles_paid": None,
            "record": {f: "" for f in load_modes()["record_fields"]},
        },
    }
    return t


# ══════════════════════════════════════════════════════════════════
# 自检
# ══════════════════════════════════════════════════════════════════

def _dig(obj, path):
    cur = obj
    for seg in path.split("."):
        if isinstance(cur, dict):
            if seg not in cur:
                return None
            cur = cur[seg]
        elif isinstance(cur, list):
            i = int(seg)
            if i >= len(cur):
                return None
            cur = cur[i]
        else:
            return None
    return cur


def run_selftest(cases_path=None):
    here = os.path.dirname(os.path.abspath(__file__))
    cases_path = cases_path or os.path.join(here, os.pardir, "evals", "selftest_cases.json")
    if not os.path.exists(cases_path):
        die("自检用例不存在：%s" % cases_path)
    with open(cases_path, "r", encoding="utf-8") as fh:
        cases = json.load(fh).get("cases") or []

    passed, failed = 0, []
    for c in cases:
        name = c.get("name") or c.get("id")
        kind = c.get("kind")
        cfg = c.get("config") or {}
        pre_bad, got = [], None

        if kind == "signal":
            got = detect_signals(c.get("text") or "")
        elif kind == "gate1":
            got = judge_gate1(cfg)
        elif kind == "score":
            got = judge_score(cfg)
        elif kind == "direct":
            got = judge_direct(cfg)
        elif kind == "funnel":
            got = judge_funnel(cfg)
        elif kind == "mso":
            got = judge_mso(cfg)
        elif kind == "delivery":
            got = judge_delivery(cfg)
        elif kind == "econ":
            got = judge_econ(cfg)
        elif kind == "stop":
            got = judge_stop(cfg)
        elif kind == "assay":
            got = judge_assay(cfg)
        elif kind == "modes":
            got = judge_modes(cfg)
        elif kind == "modes_meta":
            mods = load_modes()
            got = {
                "categories": len(mods["categories"]),
                "modes": len(mods["_mode_index"]),
                "packs": len(mods["metric_packs"]),
                "mode_to_pack": len(mods.get("mode_to_pack") or {}),
                "orphan_modes": sum(1 for m in mods["_mode_index"].values()
                                    if m.get("pack") not in mods["_pack_index"]),
                "general_board_categories": len(mods["board_general"]),
                "record_fields": len(mods["record_fields"]),
                "decision_rules": len(mods["decision_table"]),
            }
        elif kind == "docs_meta":
            root = os.path.normpath(os.path.join(here, os.pardir))
            claims = []

            def _grab(rel, pattern, label):
                fp = os.path.join(root, rel)
                if not os.path.exists(fp):
                    claims.append((label, "（文件缺失）"))
                    return
                with open(fp, encoding="utf-8") as fh:
                    t = fh.read()
                m = re.search(pattern, t, re.M)
                claims.append((label, m.group(1) if m else "（未声明）"))

            _grab("SKILL.md", r"^version:\s*v?(\d+\.\d+\.\d+)", "SKILL.md frontmatter")
            _grab("SKILL.md", r"当前版本 v(\d+\.\d+\.\d+)", "SKILL.md 正文")
            _grab("README.md", r"当前版本：\*\*v(\d+\.\d+\.\d+)\*\*", "README.md")
            for fn in ("第二层模式与看板.md", "提问脚本.md", "证据分级与锚定表.md"):
                _grab(os.path.join("references", fn), r"随引擎 v(\d+\.\d+\.\d+)", "references/" + fn)
            claims.append(("gate.py VERSION", VERSION))

            stale = ["%s（%s）" % (lbl, v) for lbl, v in claims if v != VERSION]
            got = {"sources": len(claims), "stale": stale, "version": VERSION}
        elif kind == "frontmatter":
            fp = os.path.join(here, os.pardir, "SKILL.md")
            with open(fp, encoding="utf-8") as fh:
                t = fh.read()
            m = re.match(r"^---\n(.*?)\n---\n", t, re.S)
            problems, keys = [], []
            if not m:
                problems.append("缺少 frontmatter 边界（--- … ---）")
            else:
                for i, line in enumerate(m.group(1).split("\n"), 1):
                    if not line.strip() or line[0].isspace():
                        continue
                    k = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:", line)
                    if k:
                        keys.append(k.group(1))
                    else:
                        problems.append("第 %d 行顶格但不是 键:值（疑似折行破坏了 YAML）" % i)
                for req in ("name", "description", "version", "agent_created"):
                    if req not in keys:
                        problems.append("缺少必需字段 %s" % req)
                for gone in ("description_zh", "display_name_en", "slug", "summary"):
                    if gone in keys:
                        problems.append("不应再出现已废弃字段 %s" % gone)
            got = {"fields": len(keys), "problems": problems}
        elif kind == "board":
            got = judge_board(cfg)
        elif kind in ("report", "board_html"):
            out = (render_report(cfg, mask=c.get("mask")) if kind == "report"
                   else render_board(cfg, mask=c.get("mask")))
            for probe in (c.get("expect_html") or []):
                if probe not in out:
                    pre_bad.append("HTML 缺少 %r" % probe)
            for probe in (c.get("reject_html") or []):
                if probe in out:
                    pre_bad.append("HTML 不应出现 %r" % probe)
            if out.count("<div") != out.count("</div>"):
                pre_bad.append("div 标签不平衡（%d vs %d）" % (out.count("<div"), out.count("</div>")))
            if not out.startswith("<!DOCTYPE html>") or not out.rstrip().endswith("</html>"):
                pre_bad.append("HTML 结构不完整")
            if out.count("<title>") != 1 or out.count("</html>") != 1:
                pre_bad.append("title 或 html 闭合异常")
        else:
            failed.append("%s：未知用例类型 %s" % (name, kind))
            continue

        bad = list(pre_bad)
        if got is not None:
            for k, want in (c.get("expect") or {}).items():
                have = _dig(got, k)
                if isinstance(want, list):
                    if not isinstance(have, list) or sorted(have) != sorted(want):
                        bad.append("%s 期望 %r 实得 %r" % (k, want, have))
                elif have != want:
                    bad.append("%s 期望 %r 实得 %r" % (k, want, have))
        if bad:
            failed.append("%s：%s" % (name, "；".join(bad)))
        else:
            passed += 1

    for line in failed:
        sys.stderr.write("  ✗ %s\n" % line)
    print("selftest: %d/%d 通过（%s v%s）" % (passed, passed + len(failed), ENGINE, VERSION))
    return 0 if not failed else 1


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════

def main(argv=None):
    p = argparse.ArgumentParser(prog="gate.py", description="OPC 启动验证器 · 判据引擎")
    sub = p.add_subparsers(dest="cmd")

    for name, help_text in (
        ("gate1", "闸门 1：硬红线、软风险、止损三数字"),
        ("score", "闸门 2：适配度评分（含证据封顶）"),
        ("direct", "闸门 3：方向生成校验 + 方向评分卡"),
        ("funnel", "闸门 3：预验证门槛 + 访谈到成交漏斗"),
        ("mso", "闸门 4：一页纸启动书 + MSO 最低标准 7 条"),
        ("delivery", "闸门 4：首 3 个客户的验证标准"),
        ("econ", "闸门 5：单位经济核算"),
        ("stop", "止损与退出监控（含替代路径）"),
        ("assay", "第一层合成结论（主命令）"),
        ("state", "读写状态档案"),
        ("report", "生成 HTML 决策书"),
        ("modes", "第二层：模式归类 + 专属指标包（需先有合格候选方向）"),
        ("board", "第二层：合并看板（五槽位 + 通用六类 + 决策）"),
    ):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--config", "-c", required=True)
        if name == "state":
            sp.add_argument("--stage", default=None)
            sp.add_argument("--note", default=None)
            sp.add_argument("--layer2", default=None, help="第二层字段 JSON 片段")
            sp.add_argument("--print", action="store_true", dest="show")
        if name in ("report", "board"):
            sp.add_argument("--mask", dest="mask", action="store_true", default=None)
            sp.add_argument("--no-mask", dest="mask", action="store_false")
        if name == "report":
            sp.add_argument("--out", "-o", required=True)
            sp.add_argument("--sync-state", action="store_true",
                            help="出报告的同时把结论写回状态档案")
            sp.add_argument("--sync-only", action="store_true",
                            help="只写回状态档案，不生成报告（--out 可省略）")
        if name == "board":
            sp.add_argument("--out", "-o", default=None, help="给出路径则输出 HTML 看板，否则打印 JSON")

    sp = sub.add_parser("signal", help="伪验证信号拦截 + 信号强度分级")
    sp.add_argument("--text", "-t", required=True)

    sp = sub.add_parser("scan", help="批量扫描多段进展描述")
    sp.add_argument("--config", "-c", required=True)

    sp = sub.add_parser("template", help="生成输入配置模板")
    sp.add_argument("--out", "-o", default=None)

    sp = sub.add_parser("selftest", help="运行判据回归自检")
    sp.add_argument("--cases", default=None)

    sub.add_parser("version", help="版本信息")

    args = p.parse_args(argv)

    if args.cmd == "version":
        print("%s v%s (schema %s)" % (ENGINE, VERSION, SCHEMA))
        return 0
    if args.cmd == "selftest":
        return run_selftest(args.cases)
    if args.cmd == "template":
        dump(template(), args.out)
        return 0
    if args.cmd == "signal":
        dump(detect_signals(args.text))
        return 0
    if args.cmd == "scan":
        dump(scan_signals(load_config(args.config).get("signal_texts")))
        return 0
    if not args.cmd:
        p.print_help()
        return 0

    cfg = load_config(args.config)

    if args.cmd == "modes":
        res = judge_modes(cfg)
        dump(res)
        if res["level"] == "BLOCKED_ORDER":
            print("第二层被顺序闸门拒绝：%s" % res["next_action"], file=sys.stderr)
            return RC_ORDER
        return 0
    if args.cmd == "board":
        if args.mask is not None:
            cfg.setdefault("user", {})["audience"] = "external" if args.mask else "internal"
        payload = layer2_payload(cfg)
        if args.out:
            html_out = render_board(cfg, mask=args.mask)
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(html_out)
            print("看板已生成：%s（%d 字节）" % (args.out, len(html_out.encode("utf-8"))))
        else:
            dump(payload["board"])
        if payload["board"].get("gated"):
            print("注意：第一层未通过（%s），本看板只作为记录模板。"
                  % payload["board"]["layer1"]["level"], file=sys.stderr)
        return 0
    if args.cmd == "gate1":
        dump(judge_gate1(cfg))
    elif args.cmd == "score":
        dump(judge_score(cfg))
    elif args.cmd == "direct":
        dump(judge_direct(cfg))
    elif args.cmd == "funnel":
        dump(judge_funnel(cfg))
    elif args.cmd == "mso":
        dump(judge_mso(cfg))
    elif args.cmd == "delivery":
        dump(judge_delivery(cfg))
    elif args.cmd == "econ":
        dump(judge_econ(cfg))
    elif args.cmd == "stop":
        dump(judge_stop(cfg))
    elif args.cmd == "assay":
        dump(judge_assay(cfg))
    elif args.cmd == "report":
        if args.mask is not None:
            # 显式给了 --mask / --no-mask，就连口径一起定：脱敏=对外交付，不脱敏=自用
            cfg.setdefault("user", {})["audience"] = "external" if args.mask else "internal"
        if not args.sync_only:
            if not args.out:
                die("--sync-only 之外必须提供 --out")
            html_out = render_report(cfg, mask=args.mask)
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(html_out)
            print("报告已生成：%s（%d 字节）" % (args.out, len(html_out.encode("utf-8"))))
        if args.sync_state or args.sync_only:
            path = state_path(cfg)
            st = state_read(path) or state_init(cfg, path)
            st = state_apply(st, cfg, note="report 同步")
            state_write(st, cfg)
            print("状态档案已同步：%s（阶段 %s）" % (path, st["stage"]))
    elif args.cmd == "state":
        path = state_path(cfg)
        st = state_read(path)
        if st is None:
            st = state_init(cfg, path)
        layer2 = None
        if getattr(args, "layer2", None):
            try:
                layer2 = json.loads(args.layer2)
            except ValueError as exc:
                die("--layer2 不是合法 JSON：%s" % exc)
        st = state_apply(st, cfg, stage=args.stage, note=args.note, layer2=layer2)
        state_write(st, cfg)
        if args.show:
            dump(st)
        else:
            print("状态档案：%s\n阶段：%s（%s）" % (path, st["stage"], STAGE_LABEL.get(st["stage"], "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
