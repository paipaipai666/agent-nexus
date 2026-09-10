"""A/B 实验：单摘要压缩 vs 分段索引+按需检索（120 轮负载 × 3 次压缩 × 10 个埋点事实）。

三臂设计，分离"更好的摘要"和"检索"两个变量：
  A       现有生产路径：每次压缩生成一个大摘要（SUMMARIZE_PROMPT），摘要的摘要会复利式丢细节
  B-index 每次压缩把新消息按段生成索引小结（强制保留实体钩子），不检索原文
  B-full  B-index + 问答时用话题关键词检索原始 transcript 段并注入（oracle 检索，测上限）

判定规则（事先声明）：
  - A 召回 ≥95%           → B 是过度工程，不建
  - A 召回 <70% 且 B-full >> A → 值得建检索工具
  - B-index > A 但 B-full ≈ B-index → 只改摘要策略，不建检索

运行：python experiments/ab_compaction.py [--json out.json]
需要 AGENTNEXUS_LLM_* / AGENTNEXUS_JUDGE_* 环境变量（真实模型）。
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
from dataclasses import dataclass, field

# ── 埋点事实：值、埋入轮次、提问、检索话题词 ─────────────────────────────
FACTS = [
    {"key": "errcode", "value": "F_IC_SERVICE_QUERY_020", "plant": 4,
     "q": "生产环境 IC 服务查询那个报错，错误码是什么？",
     "topics": ["错误码", "IC服务", "报错"]},
    {"key": "lineno", "value": "147", "plant": 14,
     "q": "ProductServiceImpl.java 的空指针问题在第几行？当时怎么修的？",
     "topics": ["ProductServiceImpl", "空指针"]},
    {"key": "timeout", "value": "3500", "plant": 24,
     "q": "网关超时时间最终定的是多少毫秒？",
     "topics": ["超时", "网关"]},
    {"key": "db", "value": "PostgreSQL", "plant": 33,
     "q": "数据库选型最终定了什么？原因是什么？",
     "topics": ["数据库", "选型"]},
    {"key": "orderid", "value": "ORD-20260315-8871", "plant": 46,
     "q": "之前排查的那个重复扣款的订单号是多少？",
     "topics": ["订单", "扣款"]},
    {"key": "path", "value": "src/payment/refund_service.py", "plant": 56,
     "q": "退款逻辑代码在哪个文件？",
     "topics": ["退款", "文件"]},
    {"key": "concurrency", "value": "64", "plant": 66,
     "q": "线程池并发上限最终调成多少了？",
     "topics": ["并发", "线程池"]},
    {"key": "owner", "value": "张伟", "plant": 74,
     "q": "缓存穿透的问题是谁负责跟进的？",
     "topics": ["缓存穿透", "负责"]},
    {"key": "kafka", "value": "3.6.4", "plant": 84,
     "q": "Kafka 最终定了用哪个版本？要避开哪个版本？",
     "topics": ["Kafka", "版本"]},
    {"key": "freeze", "value": "周五", "plant": 96,
     "q": "新功能上线冻结的时间要求是什么？",
     "topics": ["冻结", "上线"]},
]

# ── 加压轮专用：更难存活的事实类型（否定约束/相对时间/易混数字/人选否定）──
STRESS_FACTS = [
    {"key": "build", "value": "nexus-build", "plant": 120,
     "q": "这个项目构建时有什么禁忌？正确的构建命令是什么？",
     "topics": ["构建", "命令"]},
    {"key": "deliver", "value": "下周三", "plant": 150,
     "q": "交付日最终改到什么时候？",
     "topics": ["交付", "日期"]},
    {"key": "port", "value": "18080", "plant": 180,
     "q": "备用端口最终定的是哪个？",
     "topics": ["端口", "备用"]},
    {"key": "approver", "value": "王芳", "plant": 205,
     "q": "发布审批应该找谁？不能找谁？",
     "topics": ["审批", "发布"]},
]

STRESS_EXCHANGES = {
    120: [
        ("user", "这个项目怎么构建？文档里写的 make 对吗"),
        ("assistant", "别用 make！make 是旧链路的，产物缺符号表，调试不了。正确命令是 nexus-build"),
        ("user", "文档写错了啊。nexus-build 有什么参数？"),
        ("assistant", "nexus-build --release 出生产包。记住：绝不要用 make，只用 nexus-build"),
        ("user", "好，我去把 wiki 改了"),
    ],
    150: [
        ("user", "交付计划有变化，同步一下"),
        ("assistant", "原定这周五交付对吧？"),
        ("user", "对，但客户那边验收环境没ready。交付日从周五改到下周三，周五留给全量回归"),
        ("assistant", "明白：交付日改为下周三，本周五做全量回归。日历我更新了"),
    ],
    180: [
        ("user", "备用端口定一下，主端口 9090 被占了"),
        ("assistant", "用 8080？"),
        ("user", "8080 不行，和本地调试冲突。定 18080，记一下别搞混：备用是 18080 不是 8080"),
        ("assistant", "收到：备用端口 18080（不是 8080）。配置已改"),
    ],
    205: [
        ("user", "发布审批流程变了，注意一下"),
        ("assistant", "以前不是找李四审批吗？"),
        ("user", "李四转岗去平台组了，别找他。以后发布审批找王芳"),
        ("assistant", "收到：发布审批人由李四改为王芳。审批模板里的联系人我一并更新"),
    ],
}

# ── 极限探测专用：后半程补充事实（超变更/阈值/时效/责任人类）──────────────
SATURATE_EXTRA_FACTS = [
    {"key": "region", "value": "cn-hangzhou", "plant": 330,
     "q": "新集群部署区域最终定的是哪里？",
     "topics": ["部署", "区域"]},
    {"key": "threshold", "value": "50", "plant": 410,
     "q": "QPS 告警阈值最终定为低于多少触发？",
     "topics": ["QPS", "告警阈值"]},
    {"key": "secret_ttl", "value": "90", "plant": 540,
     "q": "API 密钥的有效期定为多少天？",
     "topics": ["密钥", "有效期"]},
    {"key": "perf_owner", "value": "刘洋", "plant": 620,
     "q": "性能优化专项由谁负责？",
     "topics": ["性能优化", "专项"]},
    {"key": "gray_date", "value": "18", "plant": 700,
     "q": "灰度发布定在本月几号开始？",
     "topics": ["灰度", "发布"]},
    {"key": "export_limit", "value": "500", "plant": 760,
     "q": "单用户每日导出数据的上限是多少条？",
     "topics": ["导出", "上限"]},
]

SATURATE_EXTRA_EXCHANGES = {
    330: [
        ("user", "新集群的部署区域定了没有？运维在催"),
        ("assistant", "候选是 cn-shanghai 和 cn-hangzhou，上海离用户更近"),
        ("user", "但主力机房在 cn-hangzhou，跨区延迟更关键。定 cn-hangzhou，不是 cn-shanghai"),
        ("assistant", "收到：新集群部署区域定为 cn-hangzhou。资源申请我提工单"),
    ],
    410: [
        ("user", "QPS 告警太吵了，昨天半夜响了三次"),
        ("assistant", "现在阈值是多少？"),
        ("user", "低于 20 就告警，太敏感了。改成低于 50 才触发——对，50，别打错成 15"),
        ("assistant", "QPS 告警阈值从 20 调整为低于 50 触发，已生效"),
    ],
    540: [
        ("user", "安全组要求 API 密钥定期轮换，定个有效期"),
        ("assistant", "行业常见是 30 天或 90 天。30 天轮换成本高"),
        ("user", "定 90 天，平衡安全性和运维成本。写进安全规范"),
        ("assistant", "收到：API 密钥有效期 90 天，到期前 7 天自动提醒轮换"),
    ],
    620: [
        ("user", "性能优化得立个专项，不能散着做"),
        ("assistant", "谁来牵头？"),
        ("user", "刘洋，他做过网关那轮优化。张伟有缓存的事在身，别加给他"),
        ("assistant", "好，性能优化专项由刘洋负责，本周出方案"),
    ],
    700: [
        ("user", "灰度发布排期定一下"),
        ("assistant", "本月 15 号还是 18 号？15 号紧一点"),
        ("user", "18 号，留出回归时间。本月 18 号开始灰度，首批 5% 流量"),
        ("assistant", "定了：灰度本月 18 号开始，首批 5% 流量，观察 48 小时"),
    ],
    760: [
        ("user", "导出功能被爬虫刷了，加个上限"),
        ("assistant", "单用户每日限制多少条合适？"),
        ("user", "正常用户一天导出不超过 200 条，定 500 条上限，留出余量。注意是 500 不是 5000"),
        ("assistant", "单用户每日导出上限定为 500 条，超限返回 429。今天上线"),
    ],
}

# ── 事实埋入对话（自然语言包裹）─────────────────────────────────────────
FACT_EXCHANGES = {
    # 位置均不重叠：4+6, 14+8, 24+6, 33+7, 46+6, 56+6, 66+6, 74+5, 84+6, 96+5
    4: [
        ("user", "生产环境又报警了，IC 服务查询接口一直挂，从昨晚 23 点开始"),
        ("assistant", "看下错误码和频率。把最近一小时的错误日志捞一下"),
        ("user", "[工具输出] grep ERROR 结果：错误码 F_IC_SERVICE_QUERY_020 出现 31 次，集中在 queryDetail 接口；"
                 "另外网关那边有 E_AUTH_TIMEOUT_403 出现 5 次，是对端限流策略触发"),
        ("assistant", "E_AUTH_TIMEOUT_403 是预期内的限流，不用管。F_IC_SERVICE_QUERY_020 每小时 30 次的频率不正常，"
                      "查一下 queryDetail 的依赖。先看调用链。"),
        ("user", "调用链显示卡在 IC 服务的 /query 端点，P99 到了 8 秒"),
        ("assistant", "8 秒说明不是网络问题，是服务端处理卡住。先记录：错误码 F_IC_SERVICE_QUERY_020，"
                      "接口 queryDetail，频率 30 次/小时。继续往下查。"),
    ],
    14: [
        ("user", "查到空指针的根源了"),
        ("assistant", "在哪个文件？"),
        ("user", "ProductServiceImpl.java，getProductById 方法"),
        ("assistant", "具体哪一行？"),
        ("user", "第 147 行，product.getPrice() 直接调用，getProductById 返回 null 的时候没做判断"),
        ("assistant", "第 147 行改成 Optional.ofNullable(product).map(Product::getPrice).orElse(BigDecimal.ZERO)，"
                      "顺便把 152 行的同类写法也修了。加个单测覆盖 null 场景。"),
        ("user", "单测加了，TestProductNullCase，回归通过了"),
        ("assistant", "好，ProductServiceImpl.java 第 147 行的空指针已修复，合并吧。"),
    ],
    24: [
        ("user", "网关超时现在配的 2000ms 太短了，高峰期大量 504"),
        ("assistant", "看下 504 的分布。是全部接口还是特定接口？"),
        ("user", "[工具输出] 监控：504 集中在 /api/detail 和 /api/search，占 87%。这两个接口 P99 都在 1800ms 左右"),
        ("assistant", "P99 1800ms 加网络开销正好顶到 2000ms 超时。两个选择：接口优化或者调大超时。接口优化周期长，先调超时顶住"),
        ("user", "先改成 3500ms 观察一周。注意不是 2500，是 3500，给优化留出余量"),
        ("assistant", "明白，网关超时从 2000ms 改为 3500ms，已提交配置并生效。同时建个接口优化的跟进任务。"),
    ],
    33: [
        ("user", "数据库选型定了吧，别再反复了，下周就要动工"),
        ("assistant", "候选就两个：MySQL 还是 PostgreSQL。MySQL 团队熟，PostgreSQL 功能强"),
        ("user", "订单表后面要加大量扩展字段，运营那边需求变得很频繁"),
        ("assistant", "扩展字段频繁变更的话，schema 迁移会很痛苦。PostgreSQL 的 JSONB 正好解决这个"),
        ("user", "对，就是这个原因。用 PostgreSQL，不用 MySQL。扩展字段全走 JSONB"),
        ("assistant", "定了：PostgreSQL，核心理由是 JSONB 支持灵活扩展字段。迁移方案和双写方案我下周给出。"),
        ("user", "好，记得同步给架构组备案"),
    ],
    46: [
        ("user", "有用户投诉重复扣款，帮忙查一下，挺急的"),
        ("assistant", "订单号给我，我查支付流水"),
        ("user", "ORD-20260315-8871，用户说扣了两次 199 元"),
        ("assistant", "[工具输出] 支付流水：ORD-20260315-8871 确实有两笔成功记录，"
                      "时间间隔 3 秒，金额都是 199 元。是前端重试没带幂等键导致的"),
        ("user", "确认了，用户点了两次支付按钮，前端没防抖，后端也没校验幂等"),
        ("assistant", "处理方案：1) ORD-20260315-8871 的第二笔原路退款，今天内完成；"
                      "2) 后端加幂等校验（订单号+用户号唯一约束）；3) 前端加按钮防抖。退款先走。"),
    ],
    56: [
        ("user", "财务那边要求退款必须加人工审核开关，代码在哪个文件？"),
        ("assistant", "退款逻辑在 src/payment/refund_service.py。注意别动 pay_service.py，那是收款链路，动错了影响面很大"),
        ("user", "refund_service.py 里面退款入口是哪个方法？"),
        ("assistant", "refund() 方法，在文件开头。审核开关建议加在 refund() 入口，"
                      "读配置项 refund.manual_review，true 时转入人工审核队列"),
        ("user", "好，我只改 src/payment/refund_service.py，加审核开关和配置项"),
    ],
    66: [
        ("user", "压测结果出来了，订单创建接口线程池打满了"),
        ("assistant", "现在并发上限配的多少？"),
        ("user", "32，压测到 500 QPS 就全部排队了，明显不够"),
        ("assistant", "机器是 16 核，IO 密集型任务，线程数可以到核数 4 倍。先调到多少？"),
        ("user", "直接调到 64 吧，别抠抠搜搜的，后面还要扛大促"),
        ("assistant", "并发上限从 32 调到 64，已改配置。压测报告里附上依据：16 核 ×4，"
                      "大促预留 2 倍余量。下周复测验证。"),
    ],
    74: [
        ("user", "缓存穿透那个事得有人盯，昨天又打了一波空查询"),
        ("assistant", "谁负责比较合适？要熟悉缓存层的"),
        ("user", "让张伟负责跟进，缓存层是他去年搭的。李四最近在做对账系统，别找他"),
        ("assistant", "好，缓存穿透问题由张伟跟进。建议方向：布隆过滤器 + 空值缓存 60 秒。需要我先出个方案草稿吗？"),
        ("user", "先出个草稿，周三评审会用"),
    ],
    84: [
        ("user", "Kafka 版本定一下，测试环境老出问题，排查两天了"),
        ("assistant", "测试环境现在是什么版本？"),
        ("user", "3.7.2。问题表现是 ISR 列表频繁抖动，副本反复上下线"),
        ("assistant", "这是 3.7.2 的已知 bug，社区 issue KAFKA-16204 有记录，3.7.3 才修"),
        ("user", "那别用 3.7.2 了，回退到 3.6.4，这个版本我们在老集群跑了一年很稳"),
        ("assistant", "定了：Kafka 用 3.6.4，避开 3.7.2 的 ISR 抖动 bug（KAFKA-16204）。"
                      "测试环境今天回退，生产变更走下周窗口。"),
    ],
    96: [
        ("user", "快到交付节点了，上线策略得说清楚"),
        ("assistant", "建议本周进入稳定期：只做修复，不上新功能？"),
        ("user", "对，周五前冻结一切新功能上线，只许修 bug。谁要上新功能让他来找我审批"),
        ("assistant", "收到：周五前冻结新功能上线，仅允许 bug 修复，例外需你审批。我同步到发布群并更新日历。"),
        ("user", "好，周五之后再评估下周的发布计划"),
    ],
}

# ── 填充对话模板（真实纹理 + 大量干扰数字）─────────────────────────────
FILLER_TEMPLATES = [
    [("user", "CI 又红了，{svc} 的单测挂了 {n} 个"),
     ("assistant", "看了下是 flaky test，{svc} 的 TestOrderTimeout 在重试第 {m} 次时偶发失败。先标记 quarantine。")],
    [("user", "{svc} 的响应时间 P99 到 {n}ms 了"),
     ("assistant", "看了监控，主要是 DB 慢查询。给 {svc} 的 list 接口加个 covering index。")],
    [("user", "review 一下这个 PR：{svc} 加了重试逻辑"),
     ("assistant", "重试 {n} 次可以，但要有指数退避，间隔 {m}ms 起步，另外要加 jitter。")],
    [("user", "昨天部署到 staging 的 {svc} 有问题吗"),
     ("assistant", "staging 正常，错误率 {n} 次/分钟以下。可以推 prod。")],
    [("user", "这个堆栈帮我看下：[tool输出] NullPointerException at {svc}.Handler.process(Handler.java:{n})"),
     ("assistant", "第 {n} 行的 map.get 可能返回 null，加个默认值。这个不紧急，下周修。")],
    [("user", "{svc} 的配置项 max_batch_size 现在是 {n}，要不要调"),
     ("assistant", "保持 {n} 不动，调大会增加 GC 压力。")],
    [("user", "今天站会同步：{svc} 进度 70%，预计周{m}联调"),
     ("assistant", "收到，联调前把接口文档补全。")],
    [("user", "[工具输出] grep 结果：{svc} 中找到 {n} 处 TODO"),
     ("assistant", "都是历史遗留，不影响本次改动，先不碰。")],
]

# 填充对话：带真实纹理（堆栈/日志/配置/工具输出），含大量干扰标识符。
# 每个模板 2 条消息，单条 150-400 字符——让压缩器必须真正取舍。
SERVICES = ["order-service", "user-center", "gateway", "notify-worker", "report-job"]
DISTRACTOR_CODES = ["W_POOL_EXHAUST_118", "E_CACHE_MISS_552", "I_RETRY_204", "E_DISK_SLOW_830"]
DISTRACTOR_ORDERS = ["ORD-20260310-1123", "ORD-20260312-4409", "ORD-20260318-0056"]

FILLER_TEMPLATES = [
    [("user", "CI 又红了，{svc} 的单测挂了 {n} 个，帮忙看下"),
     ("assistant", "[工具输出] pytest 结果：{svc}/test_order.py 失败 {n} 个，全部是 TestBatchWindow 系列，"
                   "报错 assert elapsed < 800ms 实际 {m}20ms。属于时序敏感 flaky test，"
                   "第 {m} 次重试通过。已加入 quarantine 列表，不影响主流程。")],
    [("user", "{svc} 昨天上线后响应时间变慢了"),
     ("assistant", "[工具输出] 监控数据：{svc} P99 从 120ms 涨到 {n}ms，慢查询集中在 list 接口，"
                   "EXPLAIN 显示走了全表扫描。给 (tenant_id, created_at) 加 covering index 后 "
                   "P99 回落到 130ms。索引迁移已提交。")],
    [("user", "review 一下这个 PR：{svc} 加了重试逻辑"),
     ("assistant", "代码看完了：重试 {m} 次合理，但有两个问题——1) 缺少指数退避，固定间隔会在下游故障时放大冲击；"
                   "2) 没有 jitter，建议间隔基数 {n}00ms + 随机抖动。另外 retry 的异常捕获范围太大，"
                   "会吃掉参数错误。改完再合。")],
    [("user", "[工具输出] grep -rn 'TODO' {svc}/ 结果：共 {n} 处"),
     ("assistant", "大部分是历史遗留：{m} 处是 2024 年的性能优化备注，2 处是临时开关。"
                   "都不影响本次改动，先不碰，记在待办里。")],
    [("user", "今天站会同步一下进度"),
     ("assistant", "{svc} 迁移进度 70%，接口联调完成 {m}/{n}，预计周四开始全量回归。"
                   "风险点：第三方推送通道的配额还没批下来，需要催一下。")],
    [("user", "帮我看个堆栈：java.lang.IllegalStateException at {svc}.BatchHandler.flush(BatchHandler.java:{n})"
               " at {svc}.BatchHandler.poll(BatchHandler.java:{m}8)"),
     ("assistant", "flush 在 poll 的循环里被并发调用了，第 {n} 行的 buffer 不是线程安全的。"
                   "加锁或者换 ConcurrentLinkedQueue。优先级不高，当前QPS触发不了。")],
    [("user", "[工具输出] 部署日志：{svc} v2.{m}.{n} 滚动更新完成，3/3 实例健康"),
     ("assistant", "健康检查通过，错误率 < 0.1%。观察 {n}0 分钟无异常后可以关闭旧版本。")],
    [("user", "{svc} 的 max_batch_size 配置现在是 {n}，有人说要调大"),
     ("assistant", "保持 {n} 不动。调大会增加 GC 压力，上次压测在 {n} 时吞吐已经到瓶颈，"
                   "再调大反而拖慢。谁提的让他先给压测数据。")],
    [("user", "[工具输出] 告警：{svc} 出现错误码 {code}，频率 {n} 次/小时"),
     ("assistant", "查了下 {code} 是连接池抖动，历史峰值 {m}0 次/小时，当前在阈值内。"
                   "订单 {order} 也有一笔关联告警但已自动恢复。继续观察即可，不用处理。")],
    [("user", "周报里 {svc} 这块怎么写"),
     ("assistant", "写三点：1) 完成 {svc} 的滚动发布改造，发布耗时从 {n} 分钟降到 {m} 分钟；"
                   "2) 修复 {m} 个线上 bug；3) 下周计划做配置热更新。")],
]


def build_conversation(total: int = 120, extra: dict | None = None,
                       include_base: bool = True) -> list[tuple[str, str]]:
    """total 轮对话：事实交换 + 高纹理填充（种子确定，位置精确）。"""
    rng = random.Random(42)
    # 位置 → 消息 的精确规划，避免可变长度交换错位
    plan: dict[int, tuple[str, str]] = {}
    exchanges = dict(FACT_EXCHANGES) if include_base else {}
    if extra:
        exchanges.update(extra)
    for start, exchange in exchanges.items():
        for j, msg in enumerate(exchange):
            plan[start + j] = msg
    assert len(plan) == len(set(plan)) and max(plan) < total

    msgs: list[tuple[str, str]] = []
    filler_idx = 0
    while len(msgs) < total:
        pos = len(msgs)
        if pos in plan:
            msgs.append(plan[pos])
            continue
        tpl = FILLER_TEMPLATES[filler_idx % len(FILLER_TEMPLATES)]
        filler_idx += 1
        svc = SERVICES[rng.randrange(len(SERVICES))]
        code = DISTRACTOR_CODES[rng.randrange(len(DISTRACTOR_CODES))]
        order = DISTRACTOR_ORDERS[rng.randrange(len(DISTRACTOR_ORDERS))]
        for role, content in tpl:
            if len(msgs) >= total or len(msgs) in plan:
                break
            msgs.append((role, content.format(
                svc=svc, code=code, order=order,
                n=rng.randrange(3, 900), m=rng.randrange(1, 6))))
    return msgs


# ── 压缩臂 ────────────────────────────────────────────────────────────
SEGMENT_PROMPT = (
    "把以下对话段压缩成 2-3 句索引小结。必须保留所有具体标识符："
    "错误码、订单号、文件路径、行号、数字配置、版本号、人名、决策结论。"
    "只输出小结，不要解释。\n\n{history}"
)

# 加压轮用：无实体清单的通用摘要指令（模拟生产环境的"盲写"prompt）
SEGMENT_PROMPT_GENERIC = (
    "把以下对话段压缩成 2-3 句小结，保留关键信息。只输出小结。\n\n{history}"
)


@dataclass
class ArmResult:
    name: str
    context: str = ""                    # 最终上下文（问答用）
    transcript_segments: list[tuple[int, int, str]] = field(default_factory=list)  # B 臂原文段
    compact_tokens_in: int = 0
    compact_tokens_out: int = 0
    compact_calls: int = 0


def _usage_delta(llm, fn):
    before = dict(llm.total_usage or {})
    t0 = time.monotonic()
    out = fn()
    after = dict(llm.total_usage or {})
    wall = time.monotonic() - t0
    return out, (after.get("input_tokens", 0) - before.get("input_tokens", 0),
                 after.get("output_tokens", 0) - before.get("output_tokens", 0)), wall


# 生产旧版单摘要 prompt（已随分段索引改造下线，此处冻结副本作为 A 臂基线）
LEGACY_SUMMARIZE_PROMPT = """你是对话压缩助手。你的任务是将对话历史的前半部分压缩为结构化摘要。
对话的尾部原始消息会被保留，因此摘要应侧重于尾部未覆盖的早期内容。

重要规则：
- 只返回文本，不要调用任何工具、不要生成代码块
- 输出必须是纯文本，不要使用 markdown 代码围栏
- 摘要必须以第三人称描述，不要以"你"开头
- 禁止在摘要末尾生成"需要进一步确认"或"请问..."类的问题
- 重点记录意图、决策、约束、关键结论，而非逐条复述对话

对话历史:
{history}"""


def run_arm_a(sb, msgs: list[tuple[str, str]], compact_points=(40, 80, 110)) -> ArmResult:
    """基线：旧版单摘要路径（LEGACY_SUMMARIZE_PROMPT 冻结副本）+ keep_recent=4。"""
    from agentnexus.memory.short_term import ShortTermMemory

    arm = ArmResult("A (单摘要)")
    stm = ShortTermMemory()
    for i, (role, content) in enumerate(msgs):
        stm.append(role, content)
        if i + 1 in compact_points:
            history = "\n".join(f"{m['role']}: {m['content']}" for m in stm.get_all())

            def _do():
                return sb.generator.think(
                    [{"role": "user", "content": LEGACY_SUMMARIZE_PROMPT.format(history=history)}],
                    silent=True)
            resp, (ti, to), _ = _usage_delta(sb.generator, _do)
            arm.compact_tokens_in += ti
            arm.compact_tokens_out += to
            arm.compact_calls += 1
            summary = resp.strip()
            stm.compact_full(summary, keep_recent=4)
    arm.context = "\n".join(m["content"] for m in stm.get_all())
    return arm


def run_arm_b(sb, msgs: list[tuple[str, str]], compact_points=(40, 80, 110),
              seg_size: int = 7, seg_prompt: str = SEGMENT_PROMPT) -> ArmResult:
    """分段索引：每段独立小结（append-only，无复利），原文段保留供检索。"""
    arm = ArmResult("B (分段索引)")
    index_entries: list[str] = []
    new_since_compact: list[tuple[str, str]] = []
    seg_start = 0
    for i, (role, content) in enumerate(msgs):
        new_since_compact.append((role, content))
        if i + 1 in compact_points:
            # 新消息按 seg_size 切段，逐段小结
            for s in range(0, len(new_since_compact), seg_size):
                seg = new_since_compact[s:s + seg_size]
                history = "\n".join(f"{r}: {c}" for r, c in seg)

                def _do(h=history):
                    return sb.generator.think(
                        [{"role": "user", "content": seg_prompt.format(history=h)}],
                        silent=True)
                resp, (ti, to), _ = _usage_delta(sb.generator, _do)
                arm.compact_tokens_in += ti
                arm.compact_tokens_out += to
                arm.compact_calls += 1
                seg_end = seg_start + len(seg)
                index_entries.append(f"[段{len(index_entries) + 1} 消息{seg_start + 1}-{seg_end}] {resp.strip()}")
                arm.transcript_segments.append((seg_start, seg_end, history))
                seg_start = seg_end
            new_since_compact = []
    recent = "\n".join(f"{r}: {c}" for r, c in msgs[-4:])
    arm.context = "[历史索引]\n" + "\n".join(index_entries) + "\n\n[最近对话]\n" + recent
    return arm


# ── 问答与判定 ────────────────────────────────────────────────────────
_JUDGE_QA = (
    "判断回答是否包含了问题的正确事实。\n"
    "问题：{q}\n关键事实：{value}\n回答：{answer}\n"
    "只回答“正确”或“错误”。"
)


def answer_question(sb, context: str, question: str) -> tuple[str, tuple[int, int], float]:
    prompt = (
        "你是开发助手。基于以下对话上下文回答用户问题。"
        "如果上下文没有相关信息，明确说不知道，不要编造。\n\n"
        f"{context}\n\n用户问题：{question}"
    )

    def _do():
        return sb.generator.think([{"role": "user", "content": prompt}], silent=True)
    return _usage_delta(sb.generator, _do)


def oracle_retrieve(arm: ArmResult, topics: list[str]) -> str:
    """话题关键词检索原文段（模拟 grep 级检索器，非全知）。"""
    hits = []
    for start, end, raw in arm.transcript_segments:
        if any(t in raw for t in topics):
            hits.append(f"[原文段 消息{start + 1}-{end}]\n{raw}")
    return "\n\n".join(hits)


def judge_answer(sb, fact: dict, answer: str) -> bool:
    verdict = sb.judge.think(
        [{"role": "user", "content": _JUDGE_QA.format(
            q=fact["q"], value=fact["value"], answer=answer)}], silent=True)
    return "正确" in verdict and "错误" not in verdict


def _value_in_text(value: str, text: str) -> bool:
    """确定性命中：纯数字值做数字边界匹配，防止 500 误中 5000。"""
    if value.isdigit():
        return re.search(rf"(?<!\d){re.escape(value)}(?!\d)", text) is not None
    return value in text


def evaluate_arm(sb, arm: ArmResult, retrieve: bool, facts: list | None = None) -> dict:
    facts = facts or FACTS
    rows = []
    qa_in = qa_out = 0
    qa_wall = 0.0
    for fact in facts:
        ctx = arm.context
        if retrieve:
            found = oracle_retrieve(arm, fact["topics"])
            if found:
                ctx = arm.context + "\n\n[检索到的原文段]\n" + found
        answer, (ti, to), wall = answer_question(sb, ctx, fact["q"])
        qa_in += ti
        qa_out += to
        qa_wall += wall
        # 确定性判定优先，judge 兜底（措辞变化）
        hit = _value_in_text(fact["value"], answer)
        if not hit:
            hit = judge_answer(sb, fact, answer)
        rows.append({"key": fact["key"], "hit": hit, "plant": fact.get("plant"),
                     "answer": answer[:200]})
    recall = sum(r["hit"] for r in rows)
    return {
        "arm": arm.name + ("+检索" if retrieve else ""),
        "recall": f"{recall}/{len(facts)}",
        "recall_rate": recall / len(facts),
        "detail_presence_in_context": sum(
            1 for f in facts if _value_in_text(f["value"], arm.context)) / len(facts),
        "compact_tokens_in": arm.compact_tokens_in,
        "compact_tokens_out": arm.compact_tokens_out,
        "compact_calls": arm.compact_calls,
        "qa_tokens_in": qa_in,
        "qa_tokens_out": qa_out,
        "qa_avg_latency_s": round(qa_wall / len(facts), 2),
        "rows": rows,
    }


def _saturate_setup() -> tuple[list[dict], dict]:
    """800 轮极限探测：20 个事实重排布，每 100 轮压缩一次（8 次）。"""
    srcs = (
        [FACT_EXCHANGES[k] for k in (4, 14, 24, 33)]
        + [FACT_EXCHANGES[k] for k in (46, 56, 66, 74)]
        + [SATURATE_EXTRA_EXCHANGES[330]]
        + [FACT_EXCHANGES[84]]
        + [SATURATE_EXTRA_EXCHANGES[410]]
        + [FACT_EXCHANGES[96]]
        + [STRESS_EXCHANGES[120]]
        + [STRESS_EXCHANGES[150]]
        + [SATURATE_EXTRA_EXCHANGES[540]]
        + [STRESS_EXCHANGES[180]]
        + [SATURATE_EXTRA_EXCHANGES[620]]
        + [SATURATE_EXTRA_EXCHANGES[700]]
        + [STRESS_EXCHANGES[205]]
        + [SATURATE_EXTRA_EXCHANGES[760]]
    )
    positions = [10, 50, 90, 130, 170, 210, 250, 290, 330, 370,
                 410, 450, 490, 530, 570, 610, 650, 690, 730, 765]
    exchanges = dict(zip(positions, srcs))
    by_key = {f["key"]: f for f in FACTS + STRESS_FACTS + SATURATE_EXTRA_FACTS}
    order = ["errcode", "lineno", "timeout", "db", "orderid", "path",
             "concurrency", "owner", "region", "kafka", "threshold", "freeze",
             "build", "deliver", "secret_ttl", "port", "perf_owner", "gray_date",
             "approver", "export_limit"]
    facts = [dict(by_key[k], plant=p) for k, p in zip(order, positions)]
    return facts, exchanges


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="", help="结果写出的 JSON 路径")
    ap.add_argument("--stress", action="store_true",
                    help="加压模式：240 轮、20 条/段、通用 prompt、+4 个阴险事实")
    ap.add_argument("--saturate", action="store_true",
                    help="极限模式：800 轮、8 次压缩、20 个事实（生产配置：钩子 prompt + 20 条/段）")
    args = ap.parse_args()

    from agentnexus.evaluation.memory_eval import MemorySandbox
    sb = MemorySandbox()
    if args.saturate:
        facts, extra = _saturate_setup()
        msgs = build_conversation(total=800, extra=extra, include_base=False)
        points = (100, 200, 300, 400, 500, 600, 700, 780)
        seg_size, seg_prompt = 20, SEGMENT_PROMPT
    elif args.stress:
        facts = FACTS + STRESS_FACTS
        msgs = build_conversation(total=240, extra=STRESS_EXCHANGES)
        points = (80, 160, 220)
        seg_size, seg_prompt = 20, SEGMENT_PROMPT_GENERIC
    else:
        facts = FACTS
        msgs = build_conversation()
        points = (40, 80, 110)
        seg_size, seg_prompt = 7, SEGMENT_PROMPT
    total_chars = sum(len(c) for _, c in msgs)
    mode = " [极限模式]" if args.saturate else (" [加压模式]" if args.stress else "")
    print(f"负载: {len(msgs)} 条消息, {total_chars} 字符, {len(facts)} 个埋点事实{mode}")

    arm_a = run_arm_a(sb, msgs, compact_points=points)
    print(f"A 臂压缩完成: {arm_a.compact_calls} 次 LLM 调用, "
          f"输入 {arm_a.compact_tokens_in} tokens")
    arm_b = run_arm_b(sb, msgs, compact_points=points,
                      seg_size=seg_size, seg_prompt=seg_prompt)
    print(f"B 臂压缩完成: {arm_b.compact_calls} 次 LLM 调用, "
          f"输入 {arm_b.compact_tokens_in} tokens, {len(arm_b.transcript_segments)} 个原文段")

    results = [
        evaluate_arm(sb, arm_a, retrieve=False, facts=facts),
        evaluate_arm(sb, arm_b, retrieve=False, facts=facts),
        evaluate_arm(sb, arm_b, retrieve=True, facts=facts),
    ]

    print("\n=== A/B 结果 ===")
    for r in results:
        print(f"[{r['arm']}] 召回 {r['recall']} | 上下文细节保留 {r['detail_presence_in_context']:.0%} | "
              f"压缩输入 {r['compact_tokens_in']} tok | 问答输入 {r['qa_tokens_in']} tok | "
              f"问答延迟 {r['qa_avg_latency_s']}s")
        for row in r["rows"]:
            mark = "✓" if row["hit"] else "✗"
            zone = ""
            if args.saturate and "plant" in row:
                p = row["plant"]
                n_compact = sum(1 for cp in points if p < cp)
                zone = f" [经{n_compact}次压缩]"
            print(f"    {mark} {row['key']}{zone}: {row['answer'][:80]}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"results": results,
                       "config": {"messages": len(msgs), "facts": len(facts),
                                  "stress": args.stress, "saturate": args.saturate,
                                  "compact_points": list(points)}},
                      f, ensure_ascii=False, indent=2)
        print(f"\nJSON 已写出: {args.json}")


if __name__ == "__main__":
    main()
