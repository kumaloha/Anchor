"""
HALO 四段汇总逻辑 DAG（v5 数据 + v4 视觉风格 + 跨段合并检测）
================================================================
布局规则：
  事实层    y=4.2  — Fact（□）
  前提层    y=2.8  — Assumption（△）+ ImplicitCondition（▽）
  子结论层  y=1.4  — 非核心 Conclusion（○）
  终极结论  y=0.0  — 核心 Conclusion（○金）、Prediction（◆）、Solution（⬡）

x 轴按 post_id 分 4 个 band，节点在 band 内均匀分布。
跨段同义节点用虚线括弧框出。
"""
from __future__ import annotations

import asyncio
import difflib
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./anchor_v4_test.db")


# ── 1. 从 DB 读取数据 ───────────────────────────────────────────────────────
async def _load():
    from anchor.database.session import AsyncSessionLocal
    from anchor.models import (
        Fact, Assumption, ImplicitCondition,
        Conclusion, Prediction, Solution, EntityRelationship, RawPost,
        Problem, Effect, Limitation,
    )
    from sqlmodel import select
    async with AsyncSessionLocal() as s:
        return dict(
            facts       = list((await s.exec(select(Fact))).all()),
            assumptions = list((await s.exec(select(Assumption))).all()),
            implicits   = list((await s.exec(select(ImplicitCondition))).all()),
            conclusions = list((await s.exec(select(Conclusion))).all()),
            predictions = list((await s.exec(select(Prediction))).all()),
            solutions   = list((await s.exec(select(Solution))).all()),
            problems    = list((await s.exec(select(Problem))).all()),
            effects     = list((await s.exec(select(Effect))).all()),
            limitations = list((await s.exec(select(Limitation))).all()),
            rels        = list((await s.exec(select(EntityRelationship))).all()),
            posts       = list((await s.exec(select(RawPost))).all()),
        )

data = asyncio.run(_load())

facts       = data["facts"]
assumptions = data["assumptions"]
implicits   = data["implicits"]
conclusions = data["conclusions"]
predictions = data["predictions"]
solutions   = data["solutions"]
problems    = data["problems"]
effects     = data["effects"]
limitations = data["limitations"]
rels        = data["rels"]

# post_id 顺序（按创建顺序排）
post_ids = sorted({obj.raw_post_id
                   for group in [facts, assumptions, implicits, conclusions, predictions,
                                 solutions, problems, effects, limitations]
                   for obj in group})
if not post_ids:
    post_ids = [p.id for p in data["posts"]]

print(f"Posts: {post_ids}")
print(f"Loaded: {len(facts)}F {len(assumptions)}A {len(implicits)}I "
      f"{len(conclusions)}C {len(predictions)}P {len(solutions)}S "
      f"{len(problems)}Q {len(effects)}E {len(limitations)}L {len(rels)} rels")

# ── 2. matplotlib ────────────────────────────────────────────────────────────
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.patheffects as pe
from matplotlib.lines import Line2D
import networkx as nx

plt.rcParams["font.family"]        = "Arial Unicode MS"
plt.rcParams["axes.unicode_minus"] = False

# ── 3. 节点样式 ──────────────────────────────────────────────────────────────
STYLE = {
    "fact":               dict(shape="s", color="#AED6F1", size=2600, layer=3),
    "assumption":         dict(shape="^", color="#A9DFBF", size=2400, layer=2),
    "implicit_condition": dict(shape="v", color="#D7BDE2", size=2400, layer=2),
    "conclusion":         dict(shape="o", color="#F9E79F", size=3200, layer=1),
    "prediction":         dict(shape="D", color="#F1948A", size=2600, layer=0),
    "solution":           dict(shape="h", color="#FAD7A0", size=2600, layer=0),
    "problem":            dict(shape="p", color="#FF9999", size=2600, layer=3),
    "effect":             dict(shape="8", color="#99CCFF", size=2600, layer=0),
    "limitation":         dict(shape="d", color="#CCCCCC", size=2400, layer=0),
}
LAYER_Y    = {3: 4.2, 2: 2.8, 1: 1.4, 0: 0.0}
LAYER_NAME = {3: "事实层", 2: "前提层", 1: "子结论层", 0: "终极结论"}

TYPE_NORM = {
    "fact": "fact", "facts": "fact",
    "assumption": "assumption", "assumptions": "assumption",
    "implicit_condition": "implicit_condition",
    "implicit_conditions": "implicit_condition",
    "conclusion": "conclusion", "conclusions": "conclusion",
    "prediction": "prediction", "predictions": "prediction",
    "solution": "solution", "solutions": "solution",
    "problem": "problem", "problems": "problem",
    "effect": "effect", "effects": "effect",
    "limitation": "limitation", "limitations": "limitation",
}

SEG_COLOR = {pid: c for pid, c in zip(
    post_ids,
    ["#4E9AF1", "#F1A14E", "#5DBD6A", "#C47FE0"] + ["#888888"] * 10,
)}
SEG_LABEL = {pid: lbl for pid, lbl in zip(
    post_ids,
    ["段落1  HALO跑赢M7",
     "段落2  AI不确定性→轮动",
     "段落3  护城河与避险结论",
     "段落4  内部分化：AI交易 vs HALO"],
)}

# ── 4. 构建图 ─────────────────────────────────────────────────────────────────
def nid(etype, db_id):
    return f"{etype}_{db_id}"

def short(obj, etype):
    lbl = getattr(obj, "summary", None)
    if lbl:
        return lbl
    if etype in ("fact", "conclusion", "prediction", "solution", "problem", "effect", "limitation"):
        return (getattr(obj, "claim", "") or "")[:14]
    return (getattr(obj, "condition_text", "") or "")[:14]

G = nx.DiGraph()
node_info: dict[str, dict] = {}

for f in facts:
    n = nid("fact", f.id)
    G.add_node(n)
    node_info[n] = dict(label=short(f, "fact"), etype="fact",
                        post_id=f.raw_post_id, is_core=False)

for a in assumptions:
    n = nid("assumption", a.id)
    G.add_node(n)
    node_info[n] = dict(label=short(a, "assumption"), etype="assumption",
                        post_id=a.raw_post_id, is_core=False)

for ic in implicits:
    n = nid("implicit_condition", ic.id)
    G.add_node(n)
    node_info[n] = dict(label=short(ic, "implicit_condition"), etype="implicit_condition",
                        post_id=ic.raw_post_id, is_core=False)

for c in conclusions:
    n = nid("conclusion", c.id)
    G.add_node(n)
    # 核心结论放终极层 (layer=0)，非核心放子结论层 (layer=1)
    layer = 0 if c.is_core_conclusion else 1
    node_info[n] = dict(label=short(c, "conclusion"), etype="conclusion",
                        post_id=c.raw_post_id, is_core=c.is_core_conclusion,
                        layer_override=layer)

for p in predictions:
    n = nid("prediction", p.id)
    G.add_node(n)
    node_info[n] = dict(label=short(p, "prediction"), etype="prediction",
                        post_id=p.raw_post_id, is_core=False)

for s in solutions:
    n = nid("solution", s.id)
    G.add_node(n)
    node_info[n] = dict(label=short(s, "solution"), etype="solution",
                        post_id=s.raw_post_id, is_core=False)

for pr in problems:
    n = nid("problem", pr.id)
    G.add_node(n)
    node_info[n] = dict(label=short(pr, "problem"), etype="problem",
                        post_id=pr.raw_post_id, is_core=False)

for ef in effects:
    n = nid("effect", ef.id)
    G.add_node(n)
    node_info[n] = dict(label=short(ef, "effect"), etype="effect",
                        post_id=ef.raw_post_id, is_core=False)

for lm in limitations:
    n = nid("limitation", lm.id)
    G.add_node(n)
    node_info[n] = dict(label=short(lm, "limitation"), etype="limitation",
                        post_id=lm.raw_post_id, is_core=False)

db_edges: set[tuple] = set()
for r in rels:
    st = TYPE_NORM.get(r.source_type, r.source_type)
    tt = TYPE_NORM.get(r.target_type, r.target_type)
    src = nid(st, r.source_id)
    tgt = nid(tt, r.target_id)
    if src in node_info and tgt in node_info:
        G.add_edge(src, tgt)
        db_edges.add((src, tgt))

# ── 5. 布局 ──────────────────────────────────────────────────────────────────
post_order = {pid: i for i, pid in enumerate(post_ids)}
n_posts = len(post_ids)

# 多段落：按 post band 分列；单段落：按 layer 内均匀横向排列
USE_POST_BANDS = n_posts > 1

POST_BAND_CX = [-10.0, -3.8, 2.8, 9.5]
BAND_HALF    = 2.0

# 每个 (pidx, layer) 或 (layer,) 单元内的节点列表
cell_nodes: dict[tuple, list] = defaultdict(list)
for n, info in node_info.items():
    pidx  = post_order.get(info["post_id"], 0)
    layer = info.get("layer_override", STYLE[info["etype"]]["layer"])
    key   = (pidx, layer) if USE_POST_BANDS else (layer,)
    cell_nodes[key].append(n)

NODE_SPACING = 2.6  # 节点间距（单段落模式）

pos: dict[str, tuple] = {}
for key, nodes in cell_nodes.items():
    layer = key[-1]  # 最后一个元素是 layer
    y = LAYER_Y[layer]

    if USE_POST_BANDS:
        pidx = key[0]
        cx   = POST_BAND_CX[min(pidx, len(POST_BAND_CX) - 1)]
        if len(nodes) == 1:
            pos[nodes[0]] = (cx, y)
        else:
            step = min(BAND_HALF * 2 / (len(nodes) - 1), 2.4)
            half = step * (len(nodes) - 1) / 2
            for i, n in enumerate(nodes):
                pos[n] = (cx - half + i * step, y)
    else:
        # 单段落：全部节点均匀分布在整个宽度
        total_w = (len(nodes) - 1) * NODE_SPACING
        for i, n in enumerate(nodes):
            pos[n] = (i * NODE_SPACING - total_w / 2, y)

# ── 6. 跨段同义节点检测（Merge 可视化）───────────────────────────────────────
def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()

MERGE_THRESHOLD = 0.60

merge_pairs: list[tuple[str, str]] = []
node_list = list(node_info.items())
for i, (n1, i1) in enumerate(node_list):
    for n2, i2 in node_list[i + 1:]:
        if i1["post_id"] == i2["post_id"]:
            continue
        if i1["etype"] != i2["etype"]:
            continue  # 只检测同类型实体
        if _sim(i1["label"], i2["label"]) >= MERGE_THRESHOLD:
            merge_pairs.append((n1, n2))

print(f"Merge pairs detected: {len(merge_pairs)}")
for n1, n2 in merge_pairs:
    print(f"  {node_info[n1]['label']!r} ≈ {node_info[n2]['label']!r}")

# ── 7. 绘图 ──────────────────────────────────────────────────────────────────
n_nodes = G.number_of_nodes()
n_edges = G.number_of_edges()

fig, ax = plt.subplots(figsize=(26, 13))
ax.set_title(
    f"HALO论证 — 四段汇总逻辑DAG（v5，{n_nodes}节点 / {n_edges}边）",
    fontsize=15, pad=18, fontweight="bold",
)

# 7a. 边
nx.draw_networkx_edges(
    G, pos, edgelist=list(db_edges), ax=ax,
    edge_color="#aaaaaa", arrows=True, arrowsize=18,
    width=1.5, connectionstyle="arc3,rad=0.06",
    node_size=3200,
)

# 7b. 节点（按 etype 分组绘制）
for etype, style in STYLE.items():
    nl = [n for n, info in node_info.items()
          if info["etype"] == etype]
    if not nl:
        continue
    colors = []
    for n in nl:
        info = node_info[n]
        if etype == "conclusion" and info["is_core"]:
            colors.append("#F0B429")  # 核心结论橙色
        else:
            colors.append(style["color"])
    nx.draw_networkx_nodes(
        G, pos, nodelist=nl, ax=ax,
        node_shape=style["shape"],
        node_color=colors,
        node_size=style["size"],
        alpha=0.93,
    )

# 7c. 段落色环
for n, info in node_info.items():
    if n not in pos:
        continue
    x, y = pos[n]
    rc = SEG_COLOR.get(info["post_id"], "#cccccc")
    ax.add_patch(plt.Circle(
        (x, y), 0.48,
        color=rc, fill=False, linewidth=3.0,
        transform=ax.transData, zorder=4,
    ))

# 7d. 标签（节点上方，白色 bbox）
for n, info in node_info.items():
    if n not in pos:
        continue
    x, y = pos[n]
    ax.text(
        x, y + 0.58, info["label"],
        ha="center", va="bottom",
        fontsize=8.5, color="#1a1a1a",
        bbox=dict(boxstyle="round,pad=0.22", facecolor="white",
                  edgecolor="none", alpha=0.88),
        zorder=7,
    )

# 7e. 层级标签（左侧）
for layer, y in LAYER_Y.items():
    ax.text(
        -14.2, y, LAYER_NAME[layer],
        va="center", ha="left",
        fontsize=10, color="#555", style="italic",
    )

# 7f. 终极结论强调框（支撑边最多的核心结论）
core_conclusions = [n for n, info in node_info.items()
                    if info["etype"] == "conclusion" and info["is_core"]]
if core_conclusions:
    best = max(core_conclusions, key=lambda n: G.in_degree(n))
    cx, cy = pos[best]
    ax.add_patch(mpatches.FancyBboxPatch(
        (cx - 2.4, cy - 0.62), 4.8, 1.24,
        boxstyle="round,pad=0.1", linewidth=2.2,
        edgecolor="#E87722", facecolor="none", zorder=5,
    ))

# 7g. 跨段合并括弧（虚线矩形）
for n1, n2 in merge_pairs:
    if n1 not in pos or n2 not in pos:
        continue
    x1, y1 = pos[n1]
    x2, y2 = pos[n2]
    lx, rx = min(x1, x2) - 0.7, max(x1, x2) + 0.7
    by, ty = min(y1, y2) - 0.55, max(y1, y2) + 0.55
    ax.add_patch(mpatches.FancyBboxPatch(
        (lx, by), rx - lx, ty - by,
        boxstyle="round,pad=0.05",
        linewidth=1.8, linestyle="--",
        edgecolor="#888", facecolor="#f5f5f5",
        alpha=0.25, zorder=2,
    ))
    # 连线
    ax.annotate(
        "", xy=(x2, y2), xytext=(x1, y1),
        arrowprops=dict(
            arrowstyle="<->", color="#999",
            lw=1.2, linestyle="dashed",
        ),
        zorder=3,
    )

# ── 8. 图例 ──────────────────────────────────────────────────────────────────
seg_patches = (
    [
        mpatches.Patch(color=SEG_COLOR[pid], label=SEG_LABEL.get(pid, f"段落{i+1}"))
        for i, pid in enumerate(post_ids)
        if pid in SEG_COLOR
    ]
    if n_posts > 1 else []
)
type_patches = [
    mpatches.Patch(color="#AED6F1", label="■ 事实"),
    mpatches.Patch(color="#A9DFBF", label="▲ 假设条件"),
    mpatches.Patch(color="#D7BDE2", label="▼ 隐含条件"),
    mpatches.Patch(color="#F9E79F", label="● 结论"),
    mpatches.Patch(color="#F0B429", label="● 核心结论"),
    mpatches.Patch(color="#FF9999", label="⬠ 问题"),
    mpatches.Patch(color="#99CCFF", label="✦ 效果"),
    mpatches.Patch(color="#CCCCCC", label="◇ 局限"),
]
edge_patches = [
    Line2D([0], [0], color="#aaaaaa", linewidth=1.5, label="提取边"),
    Line2D([0], [0], color="#999", linewidth=1.4, linestyle="dashed", label="同义合并节点"),
]
ax.legend(
    handles=seg_patches + type_patches + edge_patches,
    loc="upper right", fontsize=9, framealpha=0.93,
    ncol=1, bbox_to_anchor=(1.0, 1.0),
)

ax.axis("off")
ax.set_xlim(-15.5, 15.0)
ax.set_ylim(-0.9, 5.3)
plt.tight_layout()
plt.savefig("halo_dag_v5.png", dpi=160, bbox_inches="tight")
print(f"Saved → halo_dag_v5.png  ({n_nodes} nodes, {n_edges} edges, {len(merge_pairs)} merge pairs)")
