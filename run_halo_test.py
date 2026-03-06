"""
HALO四段内容提取测试（v5 多步流水线）
"""
from __future__ import annotations

import asyncio
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./anchor_v4_test.db")

POSTS = [
    {
        "id": "jinyian_ai_efficiency",
        "content": """有朋自远方来。大家好，我是大家的老朋友，金融炼药师。

最近海外热议的一个话题，源自一篇关于AI的报告（Citrini Research发布的《2028全球智能危机》）。这篇报告在海外的转载量非常大，已经超过了2000万。大家之所以热议，核心是对人工智能这一轮生产力科技进步的担忧。这种担忧主要来自科技进步和生产力快速扩散，可能对大量传统岗位和技能造成冲击，并加剧社会分化，带来所谓的K型社会影响。

其实这部分内容，在我写的《见证逆潮》一书里有过详细且深入的讨论。一边是科技生产力正在以惊人的速度裂变，未来必然会提高全社会的生产效率——实际上它已经在颠覆各个行业和领域，同时也在创造前所未有的财富。另一边，大家担心的是传统岗位被取代、技能鸿沟拉大、社会分化加剧。在当前这场席卷全球的人工智能浪潮中，我们正处在一个历史性的十字路口。

把话题放大一点，这种担忧本质上讨论的是效率和公平的问题。效率和公平本身是矛盾的，但又密不可分，如今它已成为最尖锐的议题之一。在讨论这个话题之前，我觉得就像《见证逆潮》里讲到的，我们需要保持一个基准原则：社会的意识形态没有绝对的破坏和创造这两端。就像意识形态的左右一样，不是没有利弊，而是各有利弊。大家千万不要陷入绝对化的讨论，一旦陷入，就会引发激烈争论，同时也带来巨大的分歧。

效率与公平之争：从熊彼特"创造性破坏"说起
当下AI带来的这种创造性破坏，放到人类社会的百年摇摆这个大叙事里看，正如我在《见证逆潮》中描述的，它其实是从一个极端走向另一个极端。虽然目标都是朝着中间走，希望能达到既有效率又有公平的平衡状态，但事实上我们只能趋近这种状态，永远无法真正兼得。效率和公平从来不是二选一的问题。

社会总是在过犹不及的状态中动态平衡：既不能追求绝对的高效而忽视公平，也不能为了绝对的公平而扼杀创新。那么这种动态平衡怎么实现？既要让效率高效运转，又要让每个人拥有相对的安全感。

讨论这个话题，必然要提到奥地利学派的熊彼特，他的核心理论就是创造性破坏。早在1911年前后，熊彼特在《经济发展理论》中就奠定了一个基础——他讨论的其实很像我们之前聊的生产力和生产关系。他从微观层面观察到，企业家通过新产品、新方法、新市场、新组织方式驱动经济周期，这就是生产力的进步：科技进步、生产力提升催生新产品和新方法，进而构建新组织和市场，这就是生产关系的演变。

而他系统阐述并正式提出"创造性破坏"，是在1942年的《资本主义、社会主义与民主》一书中。当时大萧条刚结束，二战已经开启。他在书中论述：资本主义的过程是工业突变的过程，它不断从内部革命化经济结构，不断破坏旧的，不断创造新的——这种创造性破坏的过程，就是资本主义的本质。

回顾历史，汽车取代马车就是典型案例。当时马车行、马车夫、养马业必然经历失业和倒闭，这是破坏；但之后又创造出了汽车工厂、流水线、加油站、驾校、公路养护、保险。随着出行爆发，长途旅行成为可能，旅游业和酒店业也繁荣起来。从创新初期的高成本，到规模化生产后成本暴跌，再到市场扩大，这正是典型的创造性破坏过程。

人类社会的发展从来不是直线的，它像一个钟摆，在效率和公平之间反复摆动。从1929年的大萧条到今天的AI大浪潮，这一摆已经经历了一个完整的周期。1929年到1930年代那种极端效率主义走到了尽头，当时奉行自由放任的市场哲学，认为市场经济能自我纠错，政府什么也不用干。结果1929年大萧条，经济崩盘，系统性危机爆发，失业率飙升，贫困和极端不平等直接催生了后来的战争——一战、二战和政治动荡。

创造性破坏在当时确实有创新的潜力，但缺乏公平的缓冲，因为大家没有意识到，最终演变成了毁灭性的崩盘。但现在不可能没意识到，因为各国政府以及我们的经济学理论框架，在过去百年经历了这些过程后，是在不断思考、不断进化的。

熊彼特后来也意识到：旧的秩序需要打破，但如果破坏太剧烈，社会无法承受，系统就会崩溃。所以二战之后一直到80年代，整个钟摆大幅摆向了公平这一侧。二战后西方国家普遍采用凯恩斯主义，政府大规模干预，建立福利国家，推进累进税制，强化工会力量。这一时期成了"黄金三十年"，效率得以提升，又侧重了公平，这种动态的、接近于均衡的状态就产生了。

到了70年代末，新的问题慢慢滋生：当时美国的工会已经大而不倒，企业活力大幅下降。到了80年代，里根、撒切尔夫人登场，新自由主义开始减税、放松管制，推动全球化、金融化和科技革命，新的蛋糕又做出来了——个人电脑、PC、互联网、全球供应链。但摆回效率，最终又进入失衡——K型分化，顶层通吃，底层造成西方工业空心化、制造工人失业、收入停滞。

打不过就一定要加入！2026年你务必跟上AI革命的渗透
到了现在，我们再次面临钟摆摇摆。大模型、生成式AI，让知识型、服务型工作自动化，生产力潜力远超以往任何技术革命。同时AI开始向各行各业快速扩散和应用——这是又一次生产力推动的效率提升。但随之一定带来新问题：程序员、翻译、客服、法律等各行各业被冲击的人，他们的焦虑又出现了。

现在的智慧，不是说站在岸上骂，而是打不过就一定要加入。所以一定要学会在这样的大周期中去辨别、去游泳。现在大周期摆向了效率，你就得跟上效率，公平这件事，是交给政府去做的。

这一百年的摇摆其实证明了一点：效率和公平不是二选一，它们是互为前提的。极端效率认为市场会自动创造新岗位，别干预——这不对，至少在1929年大萧条之后就已经证明不对。极端公平说现在不要去创新，强化管制，征收科技公司重税，暂停AI应用——这也不对。两者都忽略了历史的教训。

现在AI时代，生产力开始向生产关系重构。在未来的12到18个月，就是这种生产关系重构。AI算力成本急速下降，训练成本急速下降，技术壁垒打破之后，各行各业开始出现大规模应用。埃弗雷特·罗杰斯的"科技创新的扩散"理论表明，不是所有人都能跟上：真正的创新者约2-3%，早期采纳者约15%，早期大众看到实用性才跟进，晚期大众不得不用时才跟进，落后者约20%永远不会接受。2026年已进入早期大众期，如果还处在晚期或落后，基本上就毁了。

市场是不会管这些的。市场的本质就是高效、优胜劣汰，它不负责公平。公平这件事，如果没人兜底，破坏就会演变成社会炸弹——贫富拉大、怨气堆积，反过来又会拖累效率。所以谁去负责公平？最后其实是政府去做，通过社会机制去实现。

当AI革命追求极致效率，政府如何为公平"兜底"？
所以最近大家在讨论这个东西，千万不要过于极端。既不能因为人工智能的到来过度焦虑、担心被淘汰，也不能因为科技进步可能影响很多人，就全盘否定这轮新的创造性破坏。

我们现在应该做的是缩小差距，尽量让K型社会的差异别那么大、别那么剧烈，别在短期内快速爆发，让差异性可以拉长一些。政府部门要两手抓：既要让新的生产关系爆发、去创造，又要让那些跟不上时代的人被保护好。

政府可以：奖励那些吸纳就业的企业，适当减税；开征AI红利税，对高利润的AI公司、垄断性公司征税，把资金用于再就业、社会福利保障。企业选择逐步替代而非一次性全换，给员工缓冲期。教育端迅速和生产力结合，对被甩掉的人群做再培训，做好失业救济、社会福利兜底。

我们不会重蹈1929年大萧条的覆辙。AI人工智能这个时代，不是终点，它只是一个新的起点。效率是引擎，公平是刹车。科技浪潮来了，大家别去阻挡，也没必要阻挡，因为你挡不住。我们要学会驾驭它。""",
        "author": "金融炼药师",
        "source": "weibo",
    },
]

POSTS_DISABLED = [
    {
        "id": "weibo_private_credit",
        "content": """【美国私募信贷：定时炸弹】

上周落下了两颗炸弹。以色列/美国炸弹落在了伊朗目标上，私募信贷炸弹落在了华尔街金融股身上。美国银行股遭遇了去年"解放日"以来最大的抛售，市场忧虑私募信贷坏账飙升，流动性紧张。除两颗炸弹之外，特朗普政府重新修补了被最高法院推倒的关税壁垒，英伟达业绩强劲但是AI公司却不受市场青睐，高市早苗据报内定两位支持财政扩张的人士进入日本银行政策审议委员会。

如此宏观背景下，S&P500在二月份走出了一年来最差的单月行情，美债则是一年来最好的表现。市场定价联储不会加息但有更长的暂停降息，债券收益率下探。美元综合汇率变化不大，原油价格因为伊朗局势而大涨，黄金好调，白银暴涨，有色金属亦逞强。

今年迄今，S&P500跑赢了纳斯达克，新兴市场表现好过发达市场。AI公司在基础设施领域的持续巨额投资和回报不确定性，令连续四年成为资金宠儿的M7概念黯然失色，HALO（Hard Assets，Low Obsolescence，重资产、低淘汰）交易崛起。基于AI向实体经济许多行业的颠覆性冲击，HALO交易寻找抵抗AI颠覆的资产组合。

HALO交易认为，多数轻资产行业容易在AI代理革命中受到冲击，盈利前景存在高度不确定性，而被AI淘汰机会较小的重资产行业（如公用、物流、能源及商品等）更具备护城河保护，起码在可预见的未来城池不至于被AI攻破。恰巧这些行业在过去不被资金青睐，估值也便宜。HALO交易在今年明显跑赢M7交易，更完胜软件公司板块。

HALO交易究竟意味着股市的产业轮动还是新王者的诞生？AI代理革命的确改变了商业流程，许多SAAS软件公司的盈利模式受到冲击，部分甚至面临灭绝的危险，但这只是一个行业。对于经济中多数行业，AI意味着赋能，意味着生产效率的提高。也许AI发展可能带来就业市场的结构性变化，多数实体企业的盈利模式并没有被摧毁。

更重要的是，重资产行业也许有较深的护城河，不过多数资产回报率较低，而且投资效率递减。资金更青睐于资产回报高的行业，尤其是投资效率随着规模扩大而递增的行业、企业。IT行业（尤其是AI行业）在投资效率递增上具有天然优势。在这个意义上，笔者相信HALO更像是在AI业不确定性骤增时的避险交易，而非股市投资逻辑出现了变更。

商品、能源、公用行业内部也在分化。有色金属在AI时代的需求明显增加，它们成为AI革命的受益者。更有甚者，由于地缘政治和供应链的不确定性，主要大国将不少有色金属认定为战略矿产，动用国家资金进行战略储备，进一步推高了国际价格。对电力设备的需求也因用电需求的结构性上涨而大幅增加。这些应该视作AI交易，而非HALO交易。

话题转向私募信贷。私募信贷巨头Blue Owl旗下基金BOCCII限制投资者赎回，并开始放售组合中大约三分之一的资产，触发了投资者对私募基金出现流动性危机的担心，所有上市的私募基金的股票齐跌。2008年后美国银行受到严厉监管，放款尺度大幅收紧，贷款总量二十年里增长不到50%，而不受监管的私募信贷基金放款总量则暴涨了近五倍，成为美国风险融资的新渠道。他们瞩意拥有稳定现金回报的重资产，分切开卖给高净值客户。

然而，商场、写字楼等商业地产率先出状况，现在AI基建又有风险。科技巨头之间的AI基建"军备竞赛"，除了自有资金外，他们的最大融资源头便是私募信贷。数据中心一旦建成（并租出）就可以形成强劲稳定的现金流，远高过资金成本，所以它们是私募信贷的天然标的。可是，如今市场怀疑数据中心过剩，加之近年长端利率大幅上升，赚利差变得有点尴尬。

去年下半年起，资本市场对AI基建投资的质疑声浪渐大，私募信贷募集新资金变得困难。不仅如此，现有基金的净赎回飙升，令作出长期投资的私募信贷猝然陷入流动性困难，个别基金暂停赎回更加剧了资金的恐慌。无独有偶，Anthropic推出了一系列AI应用革命，这被市场认为将重塑未来的工作模式，对现有的SAAS软件构成颠覆性冲击，而为软件商提供资金的源头之一又是私募信贷。

私募信贷崛起得到了银行收缩信贷业务、资金成本长期低下和科技出现突破的三方加持。同时基金不受银行监管条例限制，可以在市场集资，然后投向现金流稳定的重资产中。它们一方面缺少透明度，所受监管也较松弛，另一方面投资标的多为重资产，缺乏流动性和短期套现能力，而且资产与银行、投行、股市、基金有着千丝万缕的联系。这与2008年次贷危机前情况颇有相似之处。

这是否意味着私募信贷很快会全面爆发危机？笔者认为警号已经亮起，全面危机则暂时不至于。私募信贷业务主要由几家最大的基金、投行做，它们的金融实力不容小觑。私募信贷未来爆不爆，一看AI基建会不会出事，二看投资者会不会挤提私募信贷。关闭一个私募信贷基金本身不是什么大事，不过……雷曼事件也是从关闭贝尔斯登三个基金那些小事开始的。

本周焦点：1）美国二月非农，预计新增60K（上期130K），2）中国两会开幕，政府工作报告。3）英国春季预算。除此之外，留意原油市场走势和OPEC+会议，跟踪欧元区CPI以及联储褐皮书。

本文纯属个人观点，不代表所在机构的官方立场和预测，亦非投资建议或劝诱""",
        "author": "test_analyst",
        "source": "weibo",
    },
]  # POSTS_DISABLED end


async def main():
    from anchor.database.session import create_tables, AsyncSessionLocal
    from anchor.extract.extractor import Extractor
    from anchor.models import (
        RawPost, Fact, Assumption, ImplicitCondition,
        Conclusion, Prediction, Solution, EntityRelationship,
    )
    from sqlmodel import select

    await create_tables()

    extractor = Extractor()
    post_ids = []

    async with AsyncSessionLocal() as session:
        for p in POSTS:
            rp = RawPost(
                source=p["source"],
                external_id=p["id"],
                content=p["content"],
                author_name=p["author"],
                author_platform_id=p["author"],
                url=f"manual://{p['id']}",
                posted_at=datetime.datetime(2026, 3, 1),
            )
            session.add(rp)
            await session.flush()
            post_ids.append(rp.id)
            print(f"Created RawPost id={rp.id} for {p['id']}")
            await session.commit()

    print(f"\nRunning v5 extraction on {len(POSTS)} posts...")
    for pid in post_ids:
        async with AsyncSessionLocal() as session:
            rp = (await session.exec(select(RawPost).where(RawPost.id == pid))).first()
            result = await extractor.extract(rp, session)
            if result and result.is_relevant_content:
                print(
                    f"  Post {pid}: "
                    f"{len(result.facts)}F {len(result.assumptions)}A "
                    f"{len(result.implicit_conditions)}I {len(result.conclusions)}C "
                    f"{len(result.predictions)}P {len(result.solutions)}S "
                    f"{len(result.relationships)} edges"
                )
                if result.article_summary:
                    print(f"\n  📝 摘要: {result.article_summary}")
            elif result and not result.is_relevant_content:
                print(f"  Post {pid}: not relevant — {result.skip_reason}")
            else:
                print(f"  Post {pid}: skipped or error")

    # Read back all entities
    print("\n=== DB SUMMARY ===")
    async with AsyncSessionLocal() as session:
        facts       = list((await session.exec(select(Fact))).all())
        assumptions = list((await session.exec(select(Assumption))).all())
        implicits   = list((await session.exec(select(ImplicitCondition))).all())
        conclusions = list((await session.exec(select(Conclusion))).all())
        predictions = list((await session.exec(select(Prediction))).all())
        solutions   = list((await session.exec(select(Solution))).all())
        rels        = list((await session.exec(select(EntityRelationship))).all())

    print(f"\nFacts ({len(facts)}):")
    for f in facts:
        print(f"  [{f.id}] post={f.raw_post_id}  {f.summary!r}")

    print(f"\nAssumptions ({len(assumptions)}):")
    for a in assumptions:
        print(f"  [{a.id}] post={a.raw_post_id}  {a.summary!r}")

    print(f"\nImplicit Conditions ({len(implicits)}):")
    for ic in implicits:
        obs = "★consensus" if ic.is_obvious_consensus else ""
        print(f"  [{ic.id}] post={ic.raw_post_id}  {ic.summary!r} {obs}")

    print(f"\nConclusions ({len(conclusions)}):")
    for c in conclusions:
        core = "★CORE " if c.is_core_conclusion else "      "
        cycle = "⚡CYCLE" if c.is_in_cycle else ""
        print(f"  {core}[{c.id}] post={c.raw_post_id}  {c.summary!r} {cycle}")

    print(f"\nPredictions ({len(predictions)}):")
    for p in predictions:
        print(f"  [{p.id}] post={p.raw_post_id}  {p.summary!r}  temporal={p.temporal_validity}")

    print(f"\nSolutions ({len(solutions)}):")
    for s in solutions:
        print(f"  [{s.id}] post={s.raw_post_id}  {s.summary!r}")

    print(f"\nRelationships ({len(rels)}):")
    for r in rels:
        print(f"  {r.source_type}[{r.source_id}] → {r.target_type}[{r.target_id}]  ({r.edge_type})")

    # Store for DAG generation
    globals().update({
        "_facts": facts, "_assumptions": assumptions,
        "_implicits": implicits, "_conclusions": conclusions,
        "_predictions": predictions, "_solutions": solutions,
        "_rels": rels, "_post_ids": post_ids,
    })


asyncio.run(main())

# ─── Run DAG generator ────────────────────────────────────────────────────────
print("\nGenerating DAG...")
import subprocess, sys
subprocess.run([sys.executable, "gen_dag.py"], check=True)
sys.exit(0)

# (DAG code moved to gen_dag.py)
import matplotlib

facts       = globals()["_facts"]
assumptions = globals()["_assumptions"]
implicits   = globals()["_implicits"]
conclusions = globals()["_conclusions"]
predictions = globals()["_predictions"]
solutions   = globals()["_solutions"]
rels        = globals()["_rels"]
post_ids    = globals()["_post_ids"]

plt.rcParams["font.family"]        = "Arial Unicode MS"
plt.rcParams["axes.unicode_minus"] = False

SEG_COLOR = {
    post_ids[0]: "#4E9AF1",
    post_ids[1]: "#F1A14E",
    post_ids[2]: "#5DBD6A",
    post_ids[3]: "#C47FE0",
}
SEG_LABEL = {
    post_ids[0]: "段落1 HALO跑赢M7",
    post_ids[1]: "段落2 AI不确定性→轮动",
    post_ids[2]: "段落3 护城河与避险结论",
    post_ids[3]: "段落4 内部分化：AI交易 vs HALO",
}

STYLE = {
    "fact":               dict(shape="s", color="#AED6F1", size=2000, layer=3),
    "assumption":         dict(shape="^", color="#A9DFBF", size=2000, layer=2),
    "implicit_condition": dict(shape="v", color="#D7BDE2", size=2000, layer=2),
    "conclusion":         dict(shape="o", color="#F9E79F", size=2600, layer=1),
    "prediction":         dict(shape="D", color="#F1948A", size=2200, layer=0),
    "solution":           dict(shape="h", color="#FAD7A0", size=2200, layer=0),
}
LAYER_Y = {3: 4.2, 2: 2.8, 1: 1.4, 0: 0.0}

TYPE_NORM = {
    "fact": "fact", "facts": "fact",
    "assumption": "assumption", "assumptions": "assumption",
    "implicit_condition": "implicit_condition",
    "implicit_conditions": "implicit_condition",
    "conclusion": "conclusion", "conclusions": "conclusion",
    "prediction": "prediction", "predictions": "prediction",
    "solution": "solution", "solutions": "solution",
}

def nid(etype, db_id):
    return f"{etype}_{db_id}"

def short_label(obj, etype):
    lbl = getattr(obj, "summary", None)
    if lbl:
        return lbl
    if etype in ("fact", "conclusion", "prediction", "solution"):
        return (obj.claim or "")[:14]
    return (obj.condition_text or "")[:14]

G = nx.DiGraph()
node_info = {}

for f in facts:
    n = nid("fact", f.id)
    G.add_node(n)
    node_info[n] = dict(label=short_label(f, "fact"), etype="fact",
                        post_id=f.raw_post_id, is_core=False)

for a in assumptions:
    n = nid("assumption", a.id)
    G.add_node(n)
    node_info[n] = dict(label=short_label(a, "assumption"), etype="assumption",
                        post_id=a.raw_post_id, is_core=False)

for ic in implicits:
    n = nid("implicit_condition", ic.id)
    G.add_node(n)
    node_info[n] = dict(label=short_label(ic, "implicit_condition"), etype="implicit_condition",
                        post_id=ic.raw_post_id, is_core=False)

for c in conclusions:
    n = nid("conclusion", c.id)
    G.add_node(n)
    node_info[n] = dict(label=short_label(c, "conclusion"), etype="conclusion",
                        post_id=c.raw_post_id, is_core=c.is_core_conclusion)

for p in predictions:
    n = nid("prediction", p.id)
    G.add_node(n)
    node_info[n] = dict(label=short_label(p, "prediction"), etype="prediction",
                        post_id=p.raw_post_id, is_core=False)

for s in solutions:
    n = nid("solution", s.id)
    G.add_node(n)
    node_info[n] = dict(label=short_label(s, "solution"), etype="solution",
                        post_id=s.raw_post_id, is_core=False)

for r in rels:
    st = TYPE_NORM.get(r.source_type, r.source_type)
    tt = TYPE_NORM.get(r.target_type, r.target_type)
    src = nid(st, r.source_id)
    tgt = nid(tt, r.target_id)
    if src in node_info and tgt in node_info:
        G.add_edge(src, tgt)

# ── Layout: spring per layer, then position by layer ─────────────────────────
layer_nodes = {3: [], 2: [], 1: [], 0: []}
for n, info in node_info.items():
    layer = STYLE[info["etype"]]["layer"]
    layer_nodes[layer].append(n)

pos = {}
for layer, nodes in layer_nodes.items():
    y = LAYER_Y[layer]
    for i, n in enumerate(nodes):
        x = (i - (len(nodes) - 1) / 2.0) * 2.8
        pos[n] = (x, y)

# ── Draw ──────────────────────────────────────────────────────────────────────
n_nodes = G.number_of_nodes()
n_edges = G.number_of_edges()
fig, ax = plt.subplots(figsize=(22, 12))
ax.set_title(
    f"HALO四段论证 — 汇总逻辑 DAG（v5，{n_nodes} 节点 / {n_edges} 边）",
    fontsize=14, pad=18, fontweight="bold",
)

nx.draw_networkx_edges(
    G, pos, ax=ax,
    edge_color="#aaaaaa", arrows=True, arrowsize=15,
    width=1.3, connectionstyle="arc3,rad=0.06", node_size=2600,
)

for etype, style in STYLE.items():
    nl = [n for n, info in node_info.items() if info["etype"] == etype]
    if not nl:
        continue
    colors = [
        "#F0B429" if (etype == "conclusion" and node_info[n]["is_core"])
        else style["color"]
        for n in nl
    ]
    nx.draw_networkx_nodes(
        G, pos, nodelist=nl, ax=ax,
        node_shape=style["shape"], node_color=colors,
        node_size=style["size"], alpha=0.93,
    )

# Segment color rings
for n, info in node_info.items():
    x, y = pos[n]
    rc = SEG_COLOR.get(info["post_id"], "#cccccc")
    ax.add_patch(plt.Circle((x, y), 0.44, color=rc, fill=False,
                             linewidth=2.8, transform=ax.transData, zorder=4))

# Labels above nodes
for n, info in node_info.items():
    x, y = pos[n]
    ax.text(x, y + 0.52, info["label"],
            ha="center", va="bottom", fontsize=7.8,
            color="#1a1a1a",
            bbox=dict(boxstyle="round,pad=0.2", facecolor="white",
                      edgecolor="none", alpha=0.88),
            zorder=6)

# Layer labels
for layer, y in LAYER_Y.items():
    lbl = {3: "事实层", 2: "前提层", 1: "结论层", 0: "预测/方案层"}[layer]
    xmin = min((pos[n][0] for n in layer_nodes[layer]), default=0)
    ax.text(xmin - 2.2, y, lbl, va="center", ha="right",
            fontsize=9, color="#555", style="italic")

# Legend
seg_patches = [
    mpatches.Patch(color=SEG_COLOR[pid], label=SEG_LABEL[pid])
    for pid in post_ids
    if pid in SEG_COLOR
]
type_patches = [
    mpatches.Patch(color="#AED6F1", label="■ 事实"),
    mpatches.Patch(color="#A9DFBF", label="▲ 假设条件"),
    mpatches.Patch(color="#D7BDE2", label="▼ 隐含条件"),
    mpatches.Patch(color="#F9E79F", label="● 结论"),
    mpatches.Patch(color="#F0B429", label="● 核心结论★"),
    mpatches.Patch(color="#F1948A", label="◆ 预测"),
    mpatches.Patch(color="#FAD7A0", label="⬡ 解决方案"),
]
ax.legend(handles=seg_patches + type_patches,
          loc="upper right", fontsize=8, framealpha=0.92, ncol=1)

ax.axis("off")
plt.tight_layout()
plt.savefig("halo_dag_v5.png", dpi=160, bbox_inches="tight")
print("\nDAG saved to halo_dag_v5.png")
