# 编码代理“开源优先”工作流：可执行机制与强制边界

调研日期：2026-09-29
目标：让编码代理在实现新能力前，先检索成熟开源项目或依赖，再明确选择“复用、扩展或自研”。本文只引用官方文档、官方仓库和一手项目资料。

## 结论

目前没有一个成熟通用工具，能够**证明**编码代理已经穷尽相关开源方案，并自动判断某个候选是否应当复用。开源检索是语义性、开放世界问题：搜索词、索引覆盖、许可证、维护状态、架构适配度和隐性成本都可能改变结论。

但可以把它做成一个可靠的工程门：

1. 用仓库级 `AGENTS.md` 声明何时必须执行“开源优先”流程；
2. 用一个 repo-scoped Skill 定义可重复的搜索、评分和决策步骤；
3. 要求每次新增模块/依赖/基础设施前提交机器可读的 `OSS_EVALUATION` 决策记录；
4. 用 CI 校验记录是否存在、字段是否完整、链接是否合法、与改动范围是否匹配；
5. 把 CI 设为 GitHub required status check，并让相关决策文件受 `CODEOWNERS` 审批；
6. 新引入依赖再经过漏洞、许可证和供应链健康检查。

这样能真正强制的是：**没有合规证据就不能合并。** 不能真正强制的是：代理搜索得足够全面、对候选的评价一定正确、或它在写第一行代码之前已经完成搜索。后者只能通过工作流设计、审计证据和人工抽查提高可信度。

## Codex 原生机制

### `AGENTS.md`：高可靠的持久指令，但不是安全边界

OpenAI 官方说明，Codex CLI 会自动枚举 `AGENTS.md`，从全局目录、仓库根目录到当前工作目录逐层注入对话，并以后层目录覆盖前层；模型经过训练会密切遵守这些指令。[OpenAI：Using agents.md](https://developers.openai.com/api/docs/guides/latest-model#using-agentsmd)

官方也给出用 `AGENTS.md` 指示 Codex 何时必须采用 `PLANS.md`/ExecPlan 的示例，说明它适合承载跨任务的仓库工作契约。[OpenAI：Using PLANS.md for multi-hour problem solving](https://developers.openai.com/cookbook/articles/codex_exec_plans)

适合写入：

```md
## Open-source-first gate

For any change that introduces a new module, reusable subsystem, external
dependency, protocol adapter, or infrastructure component, complete the
repository's `open-source-first` skill before implementation.

Record at least three search queries, the viable candidates found, license,
maintenance evidence, fit/gaps, and the decision: reuse, extend, or build.
Do not start implementation until `docs/decisions/oss/<id>.yaml` exists.
```

强制程度：**代理行为层面的强提示**。它会稳定进入上下文，但仍是自然语言指令；不能像权限系统一样阻止文件写入，也不能独立阻止合并。

OpenAI 也提醒，不应在 `AGENTS.md` 中要求每个微小编辑都读取大量文档，否则会造成上下文浪费。规则应限定在“新增能力、依赖、适配器、基础设施、重要重构”，而不是拼写修正和局部 bug fix。[OpenAI：Rethinking skills and prompts](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)

### Repo-scoped Skill：把流程做成可测试工作流，仍不是硬门

官方将 Skill 定义为包含 `SKILL.md`、可选脚本、模板和参考资料的可复用工作流，可规定工具调用顺序、如何处理不完整结果和输出格式。[OpenAI Skills 文档](https://developers.openai.com/plugins/concepts/skills)

官方的技能评测文章说明，repo-scoped skill 可放在 `.codex/skills/<name>/SKILL.md`，skill 的名称和描述是触发的主要信号，因此触发条件必须具体，并可通过 eval 系统测试触发率和执行质量。[OpenAI：Testing Agent Skills Systematically](https://developers.openai.com/blog/eval-skills)

建议 skill 输出：

- 功能边界与搜索词；
- GitHub repository/code 搜索结果；
- 语言生态包索引结果；
- 候选许可证、最近发布/提交、维护者/组织、文档和测试情况；
- 安全与供应链证据；
- 集成成本、缺口和退出成本；
- `reuse | extend | build` 决策与理由；
- 如果自研，列出候选无法满足的具体、可验证需求。

强制程度：**比单段提示更一致、可测试，但仍属于模型遵循层**。Skill 可以附带脚本并生成证据文件；只有证据文件再被 CI 验证，才形成硬门。

### MCP / 搜索工具：提供能力，不提供义务

Skill 可以要求调用 GitHub、文档或包索引 MCP/CLI，但“工具存在”不代表代理一定使用。OpenAI 官方对 Docs MCP 的建议也是在 `AGENTS.md` 中添加“相关任务必须查询”的指令，说明可靠调用仍依赖指令与触发机制。[OpenAI Docs MCP](https://developers.openai.com/learn/docs-mcp)

强制程度：**仅能力与提示**，除非外层 agent harness 把搜索步骤写成不可跳过的状态机。

## 可用于检索和候选评估的官方工具

### GitHub CLI / API

官方 `gh search repos` 支持关键词、语言、许可证、star、更新时间、是否归档等过滤，并可输出 JSON；`gh search code` 可查找已有实现模式。[GitHub CLI：search repos](https://cli.github.com/manual/gh_search_repos)、[search code](https://cli.github.com/manual/gh_search_code)

较新的 GitHub CLI 还提供 `gh skill search`，通过 GitHub Code Search 查找公开仓库中的 `SKILL.md`。[GitHub CLI：skill search](https://cli.github.com/manual/gh_skill_search)

适合自动生成候选清单，但注意：

- star 不是质量或安全证明；
- GitHub 搜索不覆盖所有 forge、包仓库与商业开源项目；
- `gh search code` 官方明确仍使用 legacy code search，可能与网页结果不同；
- 查询本身必须被记录，否则无法审计“搜过什么”。

强制程度：**搜索工具本身不强制；CI 可强制记录其机器输出或查询日志。**

### OpenSSF / 供应链评估工具

[OpenSSF Minder](https://github.com/mindersec/minder) 可为注册仓库定义安全与供应链策略，支持自定义规则、告警或自动修复，并能结合 OSV 管理依赖风险。它适合评估“选中的依赖是否满足组织策略”，但不会替代理解需求或寻找功能候选。

[OpenSSF Allstar](https://github.com/ossf/allstar) 能持续检查仓库的安全策略和设置；其官方文档显示当前以日志、Issue、部分自动修复为主，`block` action 仍被列为尚未实现。因此不能把 Allstar 本身当作本流程的合并硬门。

GitHub 的 [Dependency Review](https://docs.github.com/en/code-security/concepts/supply-chain-security/dependency-review) 会比较 PR 新增/更新的依赖，发现已知漏洞时让 Action 失败；设为 required check 后可阻止合并。它还可以按严重度和许可证策略失败。[官方配置文档](https://docs.github.com/en/code-security/tutorials/secure-your-dependencies/customize-dependency-review-action)

强制程度：

- 候选健康度/风险信息：**证据工具**；
- 新依赖漏洞与许可证规则 + required check：**可强制合并**；
- “是否应该复用这个依赖”：**不能自动强制或正确判断**。

### ADR 工具

[adr-tools](https://github.com/npryce/adr-tools) 提供创建、关联和替代 Architecture Decision Record 的 CLI，并将决策记录保存在仓库。它适合承载“为何复用/扩展/自研”，但默认只创建 Markdown，不会检查是否遗漏候选或阻止实现。

强制程度：**记录工具，不是门**；需要自定义 linter 与 required CI 才能变成门。

## 真正的硬门在哪里

### GitHub required status checks

GitHub 官方允许分支保护或 ruleset 要求 PR、审批和指定 status checks 全部通过后才能合并。[GitHub：Managing a branch protection rule](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/managing-a-branch-protection-rule)

因此可以新增 `oss-evaluation-gate` 工作流，校验：

1. 对“新增模块/依赖/适配器/基础设施”的 diff，存在对应决策文件；
2. 决策文件符合 JSON Schema/YAML schema；
3. 至少记录若干不同查询及其执行时间；
4. 每个候选有原始 URL、许可证、维护状态、适配差距；
5. 决策字段只能是 `reuse`、`extend`、`build`；
6. `build` 必须列出不可满足的硬需求；
7. 新依赖必须通过 dependency review、许可证与漏洞门；
8. 搜索证据不能早于需求/设计版本，避免复制陈旧报告。

将该 job 设为 required status check 后，这是**真正的合并门**。但它强制的是“证据结构与审批存在”，不是证据内容必然真实或全面。

### `CODEOWNERS` + required review

GitHub 可自动请求代码所有者审阅，并在分支保护中要求 Code Owner 批准后才允许合并。[GitHub：About code owners](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)

可将以下路径交给架构/维护者团队：

```text
/docs/decisions/oss/  @org/architecture
/.github/workflows/oss-evaluation-gate.yml  @org/platform
/.codex/skills/open-source-first/  @org/architecture
/AGENTS.md  @org/architecture
```

这能防止代理通过降低规则来让自己的 PR 通过。还应保护 `CODEOWNERS` 本身；GitHub 官方也特别建议这样做。

强制程度：**配置正确且无不受控 bypass 时，可强制合并前人工审批。**

## 只能提示、不能真正强制的机制

| 机制 | 能做什么 | 为什么不是硬门 |
|---|---|---|
| `AGENTS.md` | 每次 Codex 工作时注入仓库规则 | 自然语言遵循，不是文件系统或合并权限 |
| Skill | 标准化搜索与决策流程，附带脚本/模板 | 可能未触发、被中断或输出不完整 |
| PR template / Issue form | 要求填写 OSS 候选与理由 | 可通过 API、直接 push 或删除文本绕过 |
| 本地 pre-commit hook | 在本机检查决策文件 | 通常可用 `--no-verify` 跳过，且代理环境未必安装 |
| GitHub/包索引搜索 | 找候选 | 无法证明搜索完整或判断适配性 |
| ADR | 留下决策历史 | 默认不验证内容也不阻止合并 |
| OpenSSF Scorecard/Minder | 评估候选安全与项目健康 | 不判断功能适配，也不证明已搜到最佳方案 |

## 推荐的最小可行实现

### 1. 分层规则

- `AGENTS.md`：只写触发条件与“不得在没有决策记录时开始重大实现”。
- `.codex/skills/open-source-first/SKILL.md`：完整流程、搜索渠道、评分标准和异常处理。
- `docs/decisions/oss/*.yaml`：结构化证据。
- `tools/check_oss_evaluation.*`：纯确定性校验器。
- GitHub Actions：运行校验器与 dependency review。
- ruleset/branch protection：将上述 checks 设为 required。
- `CODEOWNERS`：保护规则、Skill、校验器和决策记录。

### 2. 建议的决策 schema

```yaml
id: oss-2026-001
scope: "TTS runtime candidate ranking"
trigger: new_subsystem
requirement_revision: "sha256:..."
searched_at: "2026-09-29T10:00:00+08:00"
queries:
  - source: github_repositories
    query: '"TTS" candidate selection quality gate'
    artifact: artifacts/oss-2026-001/github-repos.json
  - source: github_code
    query: '"REVIEW_REQUIRED" TTS'
    artifact: artifacts/oss-2026-001/github-code.json
candidates:
  - name: open-tts-eval
    url: https://github.com/inworld-ai/open-tts-eval
    license: MIT
    last_verified: "2026-09-29"
    decision: extend_or_integrate
    fit: [manifest, thresholds, provenance]
    gaps: [multi_seed_generation, chinese_normalization, postprocess_recheck]
decision: extend
rationale: "Reuse evaluator concepts; build only production orchestration gaps."
review_owner: "@org/architecture"
```

### 3. 触发范围

必须触发：

- 新增第三方依赖；
- 新模块、服务、数据库、协议适配器或基础设施；
- 自研通用算法/工具；
- 预计超过约半天的独立功能；
- 替换现有依赖或框架。

可豁免：

- 明确的 bug fix；
- 测试补充；
- 文案、样式和配置值微调；
- 现有模块内部的小改动。

豁免也应在 PR 中写一个机器可读 reason code，避免每次都生成冗长调研。

## 最终判断

最可行的方案不是寻找一个“自动阻止自研”的现成代理插件，而是组合：

```text
AGENTS.md 触发规则
        ↓
repo-scoped Skill 执行检索与评估
        ↓
机器可读 OSS decision artifact
        ↓
CI schema / freshness / diff-scope 校验
        ↓
Dependency Review + 许可证/漏洞策略
        ↓
Required status check + CODEOWNERS
```

这套设计能把“先搜再造”从口号变成**可审计、不可无声绕过的合并前流程**。需要诚实保留的边界是：任何自动化都只能证明“提交了规定证据并经过审批”，不能证明全球开源生态已经被完整搜索，也不能替代架构判断。
