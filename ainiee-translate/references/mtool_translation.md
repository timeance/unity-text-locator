# MTool 扁平 JSON 翻译工作流

用于 `{"原文":"译文"}` 形式的 MTool 游戏本地化字典。先执行审计和小批试跑，再决定是否并行全量翻译。

## 目录

- [数据契约](#数据契约)
- [阶段 1：审计与可行性门](#阶段-1审计与可行性门)
- [阶段 2：解析、候选选择与直通项](#阶段-2解析候选选择与直通项)
- [阶段 3：小批试跑与估时](#阶段-3小批试跑与估时)
- [阶段 4：拆组与子代理调度](#阶段-4拆组与子代理调度)
- [阶段 5：验收、写回与术语收口](#阶段-5验收写回与术语收口)
- [阶段 6：导出与最终验证](#阶段-6导出与最终验证)
- [取消与异常恢复](#取消与异常恢复)
- [子代理提示词模板](#子代理提示词模板)

## 数据契约

把每个 MTool 条目视为不可交换的映射：

```json
{
  "左侧运行时原文 key": "右侧显示译文 value"
}
```

必须同时满足：

1. 左侧 key 永远保持逐字符不变；禁止翻译、纠错、规范化、去重或重排 key。
2. 只修改右侧 value。已有 `key != value` 的条目视为已有译文，默认保留，除非用户明确要求重译。
3. 最终输出必须与输入拥有相同条目数、相同 key 集合和相同 key 顺序。
4. 文件名、路径、哈希、脚本命令、资源标识、字符映射表、控制码、占位符和纯技术字符串必须原样直通。
5. 半句、句首、句尾和解析残片只翻译现有内容，不补全上下文。
6. MTool writer 只导出 `TRANSLATED`/`POLISHED` 条目；因此不需要翻译的直通项也必须以“译文=原 value（空值需人工判定）”写成已翻译状态，否则导出会漏 key。

## 阶段 1：审计与可行性门

先确认输入是扁平字符串字典，不要直接按“含日文字符”全量派发：技术映射表也可能含日文或汉字。

```bash
<PFX> -m ainiee_translate.mtool inspect /path/to/game.json
```

记录并向用户报告：

- 总条目数；
- `key == value` 的待判定条目数；
- `key != value` 的已有译文数；
- 空 key/value 数；
- 技术项、资源项和疑似映射表的范围或结构；
- 预计需要人工翻译的候选条目数。

在候选量很大时必须先过可行性门：不要用“创建了多少代理”代替完成时间估算。先完成阶段 3 的试跑，用真实吞吐量计算墙钟时间；把估算和替代方案（更小范围、外部 API、本地翻译模型等）告诉用户，再继续大规模投放。

## 阶段 2：解析、候选选择与直通项

显式指定 MTool 类型，避免 `.json` 内容检测歧义：

```bash
<PFX> -m ainiee_translate.parse \
  --input /path/to/game.json \
  --type Mtool \
  --out "$WORK/work/cache.json"
```

由主代理审计并生成 `candidate_indices.json`，内容可以是整数数组，也可以是含 `text_index` 的记录数组。选择时遵循：

- 默认只重译 `key == value` 且确属玩家可见源语言文本的条目；
- 保留 `key != value` 的已有译文；
- 排除路径、文件名、哈希、命令、资源键、纯数字/符号和内部字符映射；
- 对连续的大型映射区间按结构和样本判定，不只靠语言正则；
- 抽查候选边界附近条目，避免把技术区切进译文组。

生成所有非候选条目的直通写回文件：

```bash
<PFX> -m ainiee_translate.mtool passthrough \
  --cache "$WORK/work/cache.json" \
  --selection "$WORK/work/candidate_indices.json" \
  --output "$WORK/work/mtool_passthrough.json"

<PFX> -m ainiee_translate.batch write \
  "$WORK/work/cache.json" \
  "$WORK/work/mtool_passthrough.json"
```

只允许主代理执行 `batch write`。写后重新读取缓存状态，确认未翻译数恰好等于候选数，再拆组。

## 阶段 3：小批试跑与估时

先翻译 1–2 个小组并锁定风格。MTool 字符串通常短而碎，不按固定 500 行起步；推荐初始目标：

- 每组 100–200 条；
- 同时限制源文总字符数在约 2,000–6,000；
- 同一语境连续片段尽量放在同组；
- 第一批只开 1–2 个代理，验收质量和实际耗时后再扩大并发。

使用真实数据估时：

```text
预计剩余时间 ≈ 候选总行数 ÷ 已验收行吞吐率 ÷ 实际可用并发槽位
```

按完成试跑组的墙钟时间、有效行数和重做率计算。若预计耗时明显超出用户预期，暂停并报告，不要用更多排队任务掩盖吞吐不足。

## 阶段 4：拆组与子代理调度

在缓存已完成直通写回后拆分剩余未译项：

```bash
<PFX> -m ainiee_translate.mtool split \
  --cache "$WORK/work/cache.json" \
  --output-dir "$WORK/work/mtool_groups" \
  --size 150 \
  --max-chars 5000
```

脚本生成 `mtool_NNN_src.json` 和 `manifest.json`。不要让多个代理处理同一组。

### 主代理职责

- 持有唯一任务队列和唯一写回权限；禁止子代理再创建子代理。
- 先逐个尝试创建代理以发现当前真实并发上限；达到 `agent thread limit reached` 后停止重试，把余下组放入队列。
- 同时运行数以实测上限为准，不把“用户想投放 10 个”误报成“10 个同时运行”。分别报告总分组数、运行槽位数和排队数。
- 已完成代理仍占槽位；验收完成后立即关闭，再投放下一组。
- 不因 `wait` 超时而中断代理；超时只表示尚未完成。
- 仅在最终文件存在、内容稳定且验证通过后，才允许关闭一个仍在收尾的代理。

### 子代理唯一写入范围

每个子代理只能写自己的：

- `mtool_NNN_trans.json.tmp`（构建中）；
- 验证通过后原子重命名为 `mtool_NNN_trans.json`；
- 可选 `mtool_NNN_newterms.json`。

禁止子代理修改原始 MTool JSON、`cache.json`、锁定词汇表、项目提示词、风格指南或其他组文件；禁止执行 `batch write`、`export` 或合并。

### 原子交付

不要把正在增长的正式 JSON 文件当成进度。子代理先写 `.tmp`，完整构建后运行：

```bash
<PFX> -m ainiee_translate.mtool validate \
  --source "$WORK/work/mtool_groups/mtool_NNN_src.json" \
  --translation "$WORK/work/mtool_groups/mtool_NNN_trans.json.tmp"
```

验证成功后再把 `.tmp` 原子改名为正式 `_trans.json`。主代理只读取正式文件。

### 进度口径

```bash
<PFX> -m ainiee_translate.mtool progress \
  --groups-dir "$WORK/work/mtool_groups"
```

只用“已通过验证的译文行数 ÷ 候选总行数”作为主进度。完成代理数/总分组数只作辅助，因为最后一组大小可能不同；创建成功、出现临时文件、代理口头报告或文件非空都不计进度。

## 阶段 5：验收、写回与术语收口

每个正式输出都必须通过：

```bash
<PFX> -m ainiee_translate.mtool validate \
  --source "$WORK/work/mtool_groups/mtool_NNN_src.json" \
  --translation "$WORK/work/mtool_groups/mtool_NNN_trans.json"
```

验证至少覆盖：JSON 可解析、UTF-8 BOM 兼容、条数相等、`text_index` 集合与顺序完全一致、可选 `source_text` 未被改动、译文非空、换行/转义/占位符/标签计数一致。

先由主代理审阅各组 `newterms`，统一后再更新锁定表。子代理提出的术语只是候选，不能自动成为唯一真相源。

大型缓存不要让每个子代理各自写回。可把一个已验收波次合并成一个文件，再由主代理执行一次串行写回：

```bash
<PFX> -m ainiee_translate.mtool merge \
  --groups-dir "$WORK/work/mtool_groups" \
  --output "$WORK/work/mtool_all_translations.json"

<PFX> -m ainiee_translate.batch write \
  "$WORK/work/cache.json" \
  "$WORK/work/mtool_all_translations.json"
```

`merge` 默认要求所有源组都有通过验证的正式输出。若按波次写回，先把该波次文件放入独立目录再合并；无论采用逐组还是逐波次，所有缓存写入都必须串行且由主代理执行。

## 阶段 6：导出与最终验证

完成翻译后先确认无未译候选：

```bash
<PFX> -m ainiee_translate.batch read "$WORK/work/cache.json" --size 1
```

结果必须是 `[]`。随后导出：

```bash
<PFX> -m ainiee_translate.export \
  --cache "$WORK/work/cache.json" \
  --output "$WORK/out" \
  --input /path/to/game.json
```

最后验证 MTool 左右栏契约：

```bash
<PFX> -m ainiee_translate.mtool verify-output \
  --input /path/to/game.json \
  --output "$WORK/out/game_translated.json"
```

只有在以下全部成立时才报告完成：

- 输入/输出条目数相同；
- key 集合和顺序完全相同；
- 所有 value 都是字符串，非预期空值为零；
- 控制标记检查通过；
- 原始输入文件仍未被覆盖；
- 最终导出文件可解析且路径明确。

## 取消与异常恢复

- 用户取消时立即停止投放、终止等待并关闭所有运行代理；不要写回尚未验收的结果，也不要导出“部分成品”。
- 保留已验收分组和项目缓存，除非用户明确要求删除；说明哪些内容可复用、哪些只是临时文件。
- `not_found`、`shutdown`、工具超时或客户端更新都属于不确定状态。先检查正式输出、`.tmp`、代理通知和文件稳定性，再决定重派；不要立即复制任务导致同组并发写入。
- 正式文件若缺行、JSON 解析失败或仍在变化，按未完成处理，不得计入进度或写回。
- 重派只重派失败组，不重跑已验收组。

## 子代理提示词模板

> 你是 MTool 游戏文本翻译执行代理，只负责 `{GROUP}`。完整阅读 `{STYLE_GUIDE}`、`{USER_PROMPT}`、`{GLOSSARY}` 和技能的 `references/translation_rules.md`。
>
> MTool 契约：左侧原文/key 永远不变。输入 `{SRC}` 是 `{text_index, source_text}` 数组；只生成等长、同顺序的 `{text_index, translated_text}` 数组。不得修改 `text_index`、`source_text`、条目顺序、换行、转义、占位符、标签、文件名、路径或控制码。遇到半句只翻译已有片段，不补上下文。术语以锁定表为准，表外新术语记录到 `{NEWTERMS}`，不要修改锁定表。
>
> 先把完整结果写到 `{TRANS}.tmp`，运行 `python -m ainiee_translate.mtool validate --source "{SRC}" --translation "{TRANS}.tmp"`；只有验证成功后才原子改名为 `{TRANS}`。不要改原始 JSON、`cache.json`、规则文件或其他组，不要执行 `batch write`/`export`，不要创建子代理。
>
> 最终只报告正式输出路径、通过验证的条数和新术语数。
