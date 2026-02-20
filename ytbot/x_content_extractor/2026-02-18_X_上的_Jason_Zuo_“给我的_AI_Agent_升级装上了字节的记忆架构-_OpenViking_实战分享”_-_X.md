# X 上的 Jason Zuo：“给我的 AI Agent 升级装上了字节的记忆架构: OpenViking 实战分享” / X

> 原始链接: https://x.com/xxx111god/status/2023838143045136557?s=61
> 提取时间: 2026/2/18 10:06:37

---

## 图片

![图片](https://pbs.twimg.com/media/HBYU6PRXYAA--Kc?format=jpg&name=small)

![图片](https://pbs.twimg.com/media/HBYd3bBWYAAdojR?format=jpg&name=small)

![图片](https://pbs.twimg.com/media/HBYV6W8aoAACusH?format=jpg&name=small)

![图片](https://pbs.twimg.com/media/HBYWpnpWUAAPAan?format=jpg&name=small)

![图片](https://pbs.twimg.com/media/HBYX248b0AAYbHv?format=jpg&name=small)

---

[Jason Zuo](https://x.com/xxx111god)[@xxx111god](https://x.com/xxx111god)给我的 AI Agent 升级装上了字节的记忆架构: OpenViking 实战分享这几天连续写了几篇文章，讨论如何优化 AI Agent 的 session + memory 架构 — 节省 token 的同时提高输出准确性。意外收到不少关注，看来这个痛点确实普遍。今天刷到有人分享了字节跳动刚开源的 OpenViking — 一个专门做 Agent memory 管理的框架。核心思路跟我们之前的方向一致：把 memory 当文件系统管理。但他们的系统更体系化 — 有完整的 L0/L1/L2 分层架构，有 .abstract 索引机制，有自动化的 compounding 流程。在解决了"记什么"的基础上，明确了"怎么取”机制今天就来分享：我是怎么把字节这套架构接入自己的 Agent，以及真实效果如何。━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━我之前的系统（v1）我的记忆管理其实已经跑了一个多月，核心架构：已经在做的事：markdown
```
memory/
├── MEMORY.md          # 长期记忆（手动维护）
├── 2026-02-15.md      # 每日日志
├── 2026-02-16.md
└── insights/
└── 2026-02.md     # 月度洞察

```
 └── 2026-02.md # 月度洞察 每天自动记录重要对话到日志定期 compound（压缩提炼）→ insights[[[MEMORY.md](https://x.com//MEMORY.md)](https://x.com//MEMORY.md)](https://x.com//MEMORY.md) 存核心信息（偏好、项目、工具配置）还有一套 P0/P1/P2 生命周期系统P0 = 永不过期（核心身份、偏好、铁律）P1 = 90天 TTL（活跃项目、工作上下文）P2 = 30天 TTL（临时信息）每条记忆都打标签，[memory-janitor.py](https://x.com//memory-janitor.py) 每天凌晨自动扫描，过期的 P1/P2 移到 archive/。解决的问题： 记忆不会无限膨胀，过期的自动归档。没解决的问题： 即使没过期，MEMORY.md 也越来越大，每次全读太浪费。Bot 每次启动都要读 MEMORY.md 全文，这文件已经 8000+ tokens 了。大部分时候其实用不到那么多 — 比如我问天气交易bot的结算结果，它不需要知道我的报税项目进度。但它不知道"哪些该读哪些不该读"，只能全塞进去。━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━OpenViking 给我的启发字节这个框架的核心不是新概念，是把一个我们已经在讨论的模糊想法具体化了：给每个目录加一个 .abstract 文件，作为"目录索引"。我之前的 insights/ 目录，bot 要读就得读整个月度文件。加了 .abstract 之后：先读 100 tokens 的索引知道"这个月记录了 weather trading 策略、Reflex 调试经验、Mac Mini 迁移"只有问到相关问题才去读具体内容从"全读"变成"按需读"，就这么简单。类比一下：你不会打开电脑就把所有文件内容读一遍吧？先看文件夹结构，再打开需要的文件。Agent 记忆检索也该这样 — 先想"这事儿在哪个抽屉"，再去拿。━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━具体改了什么（v1 → v2）新加的两个脚本：[memory-abstract-gen.py](https://x.com//memory-abstract-gen.py) — 扫描目录，自动生成/更新 .abstract[pre-compact-extractor.py](https://x.com//pre-compact-extractor.py) — Context 压缩前把重要内容抢救到 [SESSION-STATE.md](https://x.com//SESSION-STATE.md)整个过程大概花了一个下午，大部分代码让 bot 写的，我主要在旁边提需求和测试。━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━P + L = 完整的记忆管理这时候我意识到，P0/P1/P2 和 L0/L1/L2 这两套系统是互补的：检索流程现在是：生命周期流程是：新信息 → 打 P1/P2 标签 → 90/30 天后过期 → 自动归档核心信息 → 打 P0 标签 → 永不过期两套机制叠加，既能快速找到，又不会无限膨胀。━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━现在的完整架构（v2）asciidoc
```
memory/
├── MEMORY.md          # 长期记忆（手动维护）
├── 2026-02-15.md      # 每日日志
├── 2026-02-16.md
└── insights/
└── 2026-02.md     # 月度洞察

```
 ├── .abstract # 🆕 根目录索引 (L0) ├── MEMORY.md # 长期记忆 + P0/P1/P2 标签 ├── SESSION-STATE.md # 🆕 工作缓冲区 ├── insights/ │ ├── .abstract # 🆕 洞察索引 (L0) │ └── 2026-02.md # L1 提炼 ├── lessons/ │ ├── .abstract # 🆕 教训索引 (L0) │ └── operational-lessons.jsonl # 🆕 结构化 (L1) ├── 2026-02-17.md # L2 原始日志 └── archive/ # 过期 P1/P🆕 = 这次从 OpenViking 学来后新加的━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━真实效果Token 节省：日常场景 10 倍以上。该深入的时候还是会深入，但不再无脑全加载。━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━关键洞察OpenViking 的核心不是什么高深算法，就是一个简单的认知转换：把 memory 当「文件系统」管理，不是当「文档」读。Agent 不应该每次都"读完整本书"，而是：先看目录（L0 abstract）定位章节（L1 overview）按需读详情（L2 full content）再加上生命周期管理（P0/P1/P2），就能做到：快速检索 — 不用全读自动清理 — 不会膨胀这跟人类的记忆其实很像 — 重要的事记得牢，琐碎的事会淡忘，但需要的时候还能想起来。━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━给同样在跑 Agent 的人如果你也在用 OpenClaw / Claude Code / 类似工具：不要让 memory 无限膨胀 — 定期 compound（压缩提炼）分层存储 — 热数据小而精，冷数据按需取给 bot 建个 working buffer — 压缩前能自救给记忆加 TTL — P0 永久，P1/P2 定期归档这套东西不复杂，但效果立竿见影。━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━最后字节这个 OpenViking 的思路真的很值得借鉴。Agent memory 管理会是接下来的刚需 — context 越来越长，但永远不够用。谁先把这套跑通，谁的 Agent 就能记住更多、跑得更久。