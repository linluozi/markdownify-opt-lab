> ⚠️ 本仓库未通过发布创新闸门：上游已有**同题开放 PR #273**（dugynoo，2026-08-24，
> 标题 `perf: answer the <pre> ancestor check from parent_tags`，改的就是同一行）。
> 本仓库是对该改动的**独立复现 + 逐字节差分 + 真实语料基准验证**（C 档，非新优化）。
> 数据真实、测试真实，但「新在哪」不成立，故按规则在顶部如实标注。

# markdownify-opt-lab

对 **python-markdownify**（HTML → Markdown 转换器）做一处可验证的复现验证：
`MarkdownConverter.process_tag()` 原本在每个节点上都调用 `node.find_parent('pre')`
重新向上遍历祖先链，而该函数早已通过 `parent_tags` 参数拿到了完整的祖先标签名集合。
本补丁把这一冗余的逐节点树遍历换成集合成员判断，语义等价、输出逐字节一致。

**重要披露**：这个改动**不是本轮的新想法**。上游 `matthewwithanm/python-markdownify`
存在开放 PR [#273](https://github.com/matthewwithanm/python-markdownify/pull/273)，
改动与本仓库完全相同（同一行、同一思路），2026-08-24 起未合并。本仓库的**独立价值**
仅在于：用不同的语料与方法重新验证它——4,018 个 sha256 去重独立样本逐字节差分 0 差异、
真实 HTML 语料中位 1.27x、上游 95 个测试全绿、峰值内存不变差。

Upstream: https://github.com/matthewwithanm/python-markdownify

## 本轮创新

**档位：C（复现，不是新优化）。** 上游 PR #273 已提出同一处改动，本条不构成新意。
如实说明本仓库相对上游 PR #273 的差异：

- PR #273 的测量是**合成嵌套文档**（5000 层 `<div>` × 40000 个 `<b>`，366 KB）；
  本仓库在**真实 HTML 文档**（50 KB / 109 KB / 174 KB 三份系统自带文档）与**上游测试夹具**
  上重测，得到 1.27x / 1.26x，与 PR 的结论方向一致、量级不同。
- PR #273 的等价性论证是人工说明；本仓库用**确定性差分测试**给出 4,018 个去重独立样本的
  逐字节一致结论（含边界、Unicode、超长、`<pre>` 嵌套、`h1`–`h10`、非 heading 的 `h1foo`/`h7x`），
  并给出差分测试的独立样本数与去重方式。
- 本仓库附带**可复现基准**（`benchmark/bench.py` + 原始样本 `benchmark/raw/`）与
  **一键回滚**（`patch.diff` 反向应用即可回到基线）。

若本仓库明天换成另一个有同样冗余祖先查询的上游还能一模一样地再做一遍，那它就是换皮，
这里明确承认这一点，不把它包装成新优化。

## 优化内容

`markdownify/__init__.py`，`process_tag()` 中：

```diff
-        if node.name == 'pre' or node.find_parent('pre'):
+        # parent_tags already carries every ancestor tag name, so this is
+        # equivalent to find_parent('pre') without re-walking the tree per node.
+        if node.name == 'pre' or 'pre' in parent_tags:
```

`process_tag(node, parent_tags)` 的 `parent_tags` 是 `node` 的祖先标签名集合
（由父节点的 `parent_tags_for_children` 下传，不含 `node` 自身），因此
`'pre' in parent_tags` 与 `node.find_parent('pre') is not None` 完全等价。
改动仅 1 行（diff 3 insertions / 1 deletion），未改任何公共 API、返回值语义或上游测试。

## 基准结果

同一份 `benchmark/bench.py`，真实 HTML 文档（系统自带文档，非生成）：
`small` = nodejs/api/console.html（50,161 B）、`medium` = expat reference.html
（109,019 B）、`large` = libjs-underscore/index.html（174,040 B）。
每规模 warmup 3 次 + 计时 5 次，`time.perf_counter()`，原始样本落在 `benchmark/raw/`。

| 规模 | 基线中位数 (ms) | 优化后中位数 (ms) | 加速比 |
|---|---|---|---|
| small（50 KB 真实 HTML） | 66.265 | 49.602 | **1.3359x** |
| medium（109 KB 真实 HTML） | 78.999 | 62.284 | **1.2684x** |
| large（174 KB 真实 HTML） | 174.566 | 137.824 | **1.2666x** |
| **三规模中位数（G3）** | — | — | **1.2684x** |
| 上游测试夹具（`fixtures`，G3b） | 34.042 | 27.013 | **1.2602x** |

真实语料加速比（G3b）：**1.2602x**，用的是上游 `tests/test_*.py` 中实际喂给
`markdownify()` 的 HTML 字符串（用 `ast` 从测试源码里抽出，非合成）。
合成与真实两个数字同量级，说明收益来自真实负载而非特定构造。

噪声说明：`medium` 优化后 rel-stdev 13.9%，`small` 13.9%/7.9%，来源是机器 GC 与
页错误（3.6 GB 内存下系统文档解析会触发换页）；原始样本未剔除，全部保留在
`benchmark/raw/`。

## 质量闸门（G1–G5）

| 闸门 | 判定 | 结论 |
|---|---|---|
| G1 上游测试全绿 | `python -m pytest tests -q` → **4122 passed, 0 failed, 5.16s**（含上游 95 个测试 + 本仓库差分测试） | PASS |
| G2 差分一致 | **4,018 个 sha256 去重独立样本**，`==` 且 UTF-8 字节哈希相等，**0 差异** | PASS |
| G3 中位加速 | 三规模中位 **1.2684x** ≥ 1.15x | PASS |
| G3b 真实语料加速 | 上游测试夹具 **1.2602x** ≥ 1.15x | PASS |
| G4 大规模不退化 | large **1.2666x** ≥ 0.95x | PASS |
| G5 内存不变差 | 峰值 RSS（`/usr/bin/time -v`）44,800 KB → 46,888 KB = **1.0466x** ≤ 1.10x | PASS |

### 可复现命令

```bash
# 1) 上游 + 差分测试（G1/G2）
python -m pytest tests -q

# 2) 差分样本数与逐字节一致性（G2 明细）
python -m pytest tests/test_diff.py -q -k "unique_sample_count or byte_identical"

# 3) 基线 / 优化基准（G3/G3b/G4/G5）
python3 benchmark/bench.py --impl baseline
python3 benchmark/bench.py --impl optimized

# 3 条复制即用的完整复现命令
python3 -m pytest tests -q
python3 benchmark/bench.py --impl baseline && python3 benchmark/bench.py --impl optimized
python3 -c "import json;b=json.load(open('benchmark/baseline.json'));o=json.load(open('benchmark/optimized.json'));print({s: round(b['scales'][s]['median_ms']/o['scales'][s]['median_ms'],4) for s in b['scales']})"
```

## 差分测试说明

`tests/test_diff.py` 在**同一个进程**里分别加载「反向应用 patch 的基线副本」与
「已打补丁的当前代码」，对每个样本各跑一次并比较：

- 样本来源：4000 个固定种子随机片段（标签池含 `pre`/`code`/`h1`–`h10`/`h1foo`/
  `h7x`/表格/列表等，混合属性、实体、超长文本、CJK 与西里尔文本）+ 30 个边界用例
  （空串、纯空白、无标签、`<pre>` 嵌套、`h1`–`h10`、`h1foo`、`h7x`、5000 字符长文本、
  2000 行 `<pre>`、Unicode、`<!DOCTYPE>`、注释）+ 6×6 块级元素两两组合 +
  3 份真实 HTML 文档。
- **去重方式**：对样本内容做 sha256，只保留唯一哈希；报告与断言用的是
  **去重后的独立样本数 4,018**（不是循环次数）。README 与 `tests/` 输出均以该数为准。
- **比对方式**：字符串 `==` 且 `sha256(实际输出) == sha256(基线输出)`，逐字节一致，
  没有 approx、没有截断比较。
- 另有边界一致性用例：非字符串输入（`None`/`int`/`float`/`list`）两个实现抛出同类异常，
  `bytes` 输入两个实现输出逐字节相同。

## 环境

- CPU：AMD EPYC 7K62 48-Core Processor（本机 4 vCPU）
- 内存：3,812,564 KB（3.7 GB）
- 运行时：CPython 3.12.3；pytest 9.x；beautifulsoup4（上游依赖）
- 种子：20261119（`bench.py` 固定；差分测试同样用 `random.Random(20261119)`）
- 计时：`time.perf_counter()`；聚合：中位数 + p25/p75（`statistics.quantiles(n=4, inclusive)`）

## 溯源与许可

本仓库是上游 `matthewwithanm/python-markdownify`（MIT License）的一个性能补丁副本，
包含上游源码与上游测试（未做任何修改），另加 `benchmark/`、`tests/test_diff.py`、
`patch.diff` 与本文档。`LICENSE` 保留上游 MIT 协议原文；改动范围仅为
`markdownify/__init__.py` 的 `process_tag()` 中一处祖先判断（见 `patch.diff`）。
本仓库不改变上游协议名与版权归属。
