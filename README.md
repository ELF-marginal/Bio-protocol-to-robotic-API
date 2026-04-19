
# Bio-Protocol to Lab-API Workflow System

## 1. 项目简介

本项目用于将自然语言实验协议（Bio-Protocol / Lab Protocol）转换为本地实验室机器人可执行的 API 工作流，并在执行前后完成校验、修复、执行仿真与基准测试。

系统的核心目标包括：

1. 将实验文本解析为结构化步骤（Parsed Protocol）
2. 将结构化步骤映射为实验室 API 调用序列（Workflow）
3. 对工作流进行合法性校验与自动修复
4. 在虚拟实验室状态机中执行工作流
5. 对测试集进行批量 benchmark，输出准确率、可执行率与修复统计

当前系统支持两类主要运行模式：

- 普通模式：parser → grounder → validator → repair → executor
- operation 模式：先按 operation 切分，再逐组 parser / grounder，最后由 planner 合并为最终 workflow

---

## 2. 系统整体流程

### 2.1 单次运行流程

```text
Protocol Text
    ↓
Operation Splitter（默认开启）
    ↓
Operation-level Parser
    ↓
Operation-level Grounder
    ↓
Planner（合并各 operation 的 API 组）
    ↓
Validator
    ↓
Rule Repair
    ↓
LLM Repair（可选）
    ↓
Executor
    ↓
Run Outputs / Summary Report
````

### 2.2 Benchmark 流程

```text
Benchmark Case
    ↓
Parser
    ↓
Grounder
    ↓
Validator
    ↓
Rule Repair
    ↓
LLM Repair（可选）
    ↓
Executor
    ↓
Case Evaluation
    ↓
Summary Statistics
```

---

## 3. 项目特点

### 3.1 规则后端与 LLM 后端混合设计

Parser、Grounder、Planner、Repair 都支持可选的 LLM 路径。
当 LLM 关闭、调用失败、输出 JSON 不合法或 schema 校验不通过时，系统会回退到规则路径或 fallback 逻辑。

### 3.2 支持 operation 级处理

系统可以将复杂协议按行切分为多个 operation，逐组完成解析与映射，再由 planner 合并为一个最终可执行 workflow。
这种设计适合较长协议、分段式 protocol，以及需要保留中间调试信息的场景。

### 3.3 支持工作流合法性校验与自动修复

系统在执行前会对 workflow 做规则检查，包括：

* API 是否存在
* 必需参数是否缺失
* 顺序是否违规
* 前置条件是否满足

若存在问题，系统会优先执行 rule repair；若问题仍未解决，可继续启用 LLM repair。

### 3.4 支持虚拟实验室执行

系统内置 executor，可在虚拟实验室状态机中逐条执行 API 调用，并输出：

* 执行事件流
* 最终状态
* 每一步调用后的状态快照

### 3.5 支持 benchmark 评测

系统支持批量读取测试用例，对解析、映射、顺序、参数、最终状态与执行成功率进行评估，并输出汇总统计结果。

---

## 4. 项目目录结构

```text
project_root/
├─ main.py
├─ src/
│  ├─ models/
│  │  └─ contracts.py
│  ├─ pipeline/
│  │  ├─ llm_parser.py
│  │  ├─ llm_grounder.py
│  │  ├─ llm_planner.py
│  │  ├─ llm_repair.py
│  │  ├─ validator.py
│  │  ├─ repair.py
│  │  ├─ executor.py
│  │  ├─ benchmark_runner.py
│  │  ├─ operation_splitter.py
│  │  ├─ operation_orchestrator.py
│  │  ├─ workflow_planner.py
│  │  ├─ mock_parser.py
│  │  └─ mock_grounder.py
│  └─ utils/
│     └─ io.py
├─ configs/
│  ├─ api_registry.yaml
│  ├─ initial_lab_state.yaml
│  ├─ benchmark_config.yaml
│  ├─ llm_parser_config.yaml
│  ├─ llm_grounder_config.yaml
│  ├─ llm_planner_config.yaml
│  ├─ llm_repair_config.yaml
│  ├─ llm_parser_notice.txt
│  ├─ llm_grounder_notice.txt
│  ├─ llm_planner_notice.txt
│  └─ llm_repair_notice.txt
├─ tests/
│  └─ cases/
└─ runs/
   ├─ wf_xxxxxxxx/
   └─ benchmark_YYYYMMDD_HHMMSS/
```

---

## 5. 核心文件说明

### 5.1 `main.py`

项目命令行主入口，基于 Typer 实现，提供以下命令：

* `run`：执行单条实验协议
* `version`：输出版本号
* `benchmark`：运行批量测试

`run` 支持：

* 文本输入或文件输入
* 启用/关闭 validator
* 启用/关闭 rule repair
* 启用/关闭 LLM repair
* 启用/关闭 LLM parser
* 启用/关闭 LLM grounder
* 启用/关闭 LLM planner
* 启用/关闭 operation mode

### 5.2 `operation_splitter.py`

将原始实验协议按行切分为 operation 列表，并记录：

* `operation_id`
* `raw_text`
* `line_no`
* `section_hint`
* `is_section_header`

该模块是 operation 模式的基础。

### 5.3 `operation_orchestrator.py`

负责编排 operation 级 parser 和 grounder 流程，包含两个核心过程：

* `run_operation_parser_pass`
* `run_operation_grounder_pass`

其职责包括：

* 逐组调用 parser
* 逐组调用 grounder
* 汇总 operation 级中间结果
* 展平步骤结果
* 统计 LLM 调用情况
* 汇总未注册 API 信息

### 5.4 `llm_parser.py`

负责将自然语言实验协议解析为结构化步骤。

主要功能包括：

* 文本预处理
* 单位标准化（如 `μL → uL`、`°C → C`）
* 句子切分
* parser 质量分析
* LLM 输入构造
* LLM 输出解析与 schema 校验
* fallback 到规则 parser

输出的结构化结果通常包含：

* `step_id`
* `raw_text`
* `action`
* `entities`
* `parameters`

### 5.5 `llm_grounder.py`

负责将 Parsed Protocol 映射为 API Workflow。

主要功能包括：

* 读取 API registry
* 读取实验室状态
* 构造 grounder 输入
* 调用 LLM 或规则 grounder
* 检查 workflow 结构合法性
* 检查是否存在未注册 API
* 输出标准 workflow 结构

### 5.6 `llm_planner.py`

负责将多个 operation 的 API 组整合为一个最终 workflow。

主要功能包括：

* 合并 operation-level API groups
* 去除冗余动作
* 合并相邻可优化步骤
* 自动补全隐藏但必要的操作
* 输出扁平化的最终 `workflow.api_calls`

### 5.7 `validator.py`

负责对 workflow 执行规则校验。

主要检查内容包括：

* API 是否在 registry 中存在
* 必需参数是否缺失
* 调用顺序是否正确
* 前置条件是否满足

典型校验规则示例：

* `fridge.close` 不能先于 `fridge.open`
* `pipette.transfer` 前需要 `pipette.attach_tip`
* 对 tube 转移或 mix 前，tube 需要先 uncapped
* 某些 heater 操作需要满足先设定温度等前置条件

输出通常包括：

* `valid`
* `issue_count`
* `issues`

### 5.8 `llm_repair.py`

负责在 rule repair 后，对剩余 unresolved issue 进行可选的 LLM 修复。

主要功能包括：

* 判断是否应该触发 LLM repair
* 构造最小修复输入
* 解析 LLM 输出的 patch operations
* 校验 patch 是否合法
* 将 patch 应用于 workflow
* 再次执行 validator，判断是否接受补丁

### 5.9 `executor.py`

负责执行最终 workflow。

主要功能包括：

* 读取初始实验室状态
* 逐条执行 API 调用
* 生成执行事件
* 保存执行过程中的状态快照
* 输出最终状态
* 如果某一步失败，则中断执行并返回失败结果

当前 executor 已实现一组基础实验室 API，例如：

* `fridge.open`
* `fridge.close`
* `robot.pick`
* `robot.place`
* `tube.uncap`
* `tube.cap`
* `pipette.attach_tip`
* `pipette.transfer`
* `pipette.mix`
* `pipette.discard_tip`
* `heater.set_temperature`
* `heater.place`
* `heater.remove`
* `timer.wait`

### 5.10 `benchmark_runner.py`

负责批量 benchmark。

主要功能包括：

* 读取测试用例 YAML 文件
* 对每个 case 运行 parser / grounder / repair / execution
* 进行 parsing / grounding / parameter / sequence / final_state 等评测
* 统计修复成功情况
* 统计 LLM 调用与成功情况
* 输出 benchmark 汇总与每个 case 的详细结果

---

## 6. CLI 命令

### 6.1 查看版本

```bash
python main.py version
```

### 6.2 直接输入文本运行

```bash
python main.py run --text "Take the sample tube from the fridge. Add 0.1 mL buffer to the sample tube. Incubate at 37C for 10 min."
```

### 6.3 从文件运行

```bash
python main.py run --file test.txt
```

### 6.4 指定标题

```bash
python main.py run --file test.txt --title "Demo Protocol"
```

### 6.5 启用 LLM parser / grounder / planner / repair

```bash
python main.py run \
  --file test.txt \
  --enable-llm-parser \
  --enable-llm-grounder \
  --enable-llm-planner \
  --enable-llm-repair
```

### 6.6 关闭 operation mode

```bash
python main.py run --file test.txt --enable-operation-mode False
```

### 6.7 运行 benchmark

```bash
python main.py benchmark
```

### 6.8 指定 benchmark case 目录

```bash
python main.py benchmark --cases-dir tests/cases
```

### 6.9 benchmark 中启用 LLM 能力

```bash
python main.py benchmark \
  --enable-llm-parser \
  --enable-llm-grounder \
  --enable-llm-repair
```

---

## 7. 单次运行输出说明

单次 `run` 执行结束后，系统会在如下目录生成输出文件：

```text
runs/<workflow_id>/
```

### 7.1 输入与 operation 分组相关

#### `protocol_input.json`

保存本次输入协议的原始信息，包括 protocol id、标题、原始文本和来源。

#### `operations.json`

当 operation mode 开启时生成。
保存协议按行切分后的 operation 列表。

#### `operation_parser_groups.json`

当 operation mode 开启时生成。
保存每个 operation 的 parser 结果，包括原始文本、预处理信息、LLM parser 信息与解析后的步骤。

#### `operation_grounder_groups.json`

当 operation mode 开启时生成。
保存每个 operation 的 grounder 结果，包括 workflow、API 调用组、grounding 校验信息与 LLM grounder 信息。

#### `operation_api_groups.json`

当 operation mode 开启时生成。
保存各 operation 对应的 API 调用组，供 planner 使用。

### 7.2 Parser 相关文件

#### `parser_preprocess.json`

保存 parser 预处理信息，例如文本标准化、替换次数、长度变化、是否截断等。

#### `llm_parser_result.json`

保存 parser 后端的运行情况，例如是否启用 LLM、是否 fallback、失败原因等。

#### `llm_parser_input.json`

保存发送给 LLM parser 的输入内容。

#### `llm_parser_raw_output.json`

保存 LLM parser 的原始输出。

#### `llm_parser_parsed_output.json`

保存解析后的结构化 LLM parser 输出。

### 7.3 Grounder 相关文件

#### `grounding_result.json`

保存 grounder 的总体结果，包括 backend mode、是否有效、是否包含未注册 API、失败原因等。

#### `grounding_validation_result.json`

保存对 grounder 输出进行结构和 registry 校验后的结果。

#### `llm_grounder_input.json`

保存发送给 LLM grounder 的输入内容。

#### `llm_grounder_raw_output.json`

保存 LLM grounder 的原始输出。

#### `llm_grounder_parsed_output.json`

保存归一化后的 grounder 输出。

### 7.4 Planner 相关文件

#### `planner_result.json`

保存 planner 的总体结果，包括是否启用 LLM planner、是否接受 planner 输出、是否合法等。

#### `planner_validation_result.json`

保存 planner 输出的 workflow 校验结果。

#### `llm_planner_input.json`

保存发送给 LLM planner 的输入内容。

#### `llm_planner_raw_output.json`

保存 LLM planner 的原始输出。

#### `llm_planner_parsed_output.json`

保存解析后的 planner 输出结果。

### 7.5 Workflow 与修复相关文件

#### `parsed_protocol.json`

保存最终解析得到的结构化 protocol。

#### `grounded_workflow.json`

保存 grounder 输出的 workflow。

#### `workflow.json`

保存最终进入执行阶段的 workflow。

#### `validation_before_rule_repair.json`

保存规则修复前的校验结果。

#### `validation_result.json`

保存最终 workflow 的校验结果。

#### `repair_result.json`

保存 rule repair 的执行结果和修复记录。

#### `llm_input.json`

保存发送给 LLM repair 的输入内容。

#### `llm_raw_output.json`

保存 LLM repair 的原始输出。

#### `llm_parsed_output.json`

保存解析后的 LLM repair patch 内容。

#### `workflow_before_llm_patch.json`

保存应用 LLM patch 之前的 workflow。

#### `workflow_after_llm_patch.json`

保存应用 LLM patch 之后的 workflow。

#### `llm_patch_result.json`

保存 LLM repair 是否调用、是否成功解析、是否应用补丁、是否接受补丁、失败原因等信息。

#### `llm_validation_result.json`

保存 LLM patch 前后 validation issue 数量变化情况。

### 7.6 执行结果相关文件

#### `execution_result.json`

保存执行结果，包括：

* 是否执行成功
* 已执行调用数
* 执行事件列表
* 最终状态
* 状态快照

#### `final_state.json`

保存执行结束后的实验室状态。

#### `state_snapshots.json`

保存每一步 API 调用后的状态快照。

### 7.7 文本摘要文件

#### `summary_report.md`

保存本次运行的 Markdown 摘要报告，便于快速查看：

* Protocol ID
* Workflow ID
* Executed Calls
* Success
* Validation 情况
* Repair 情况
* Parser / Grounder / Planner / LLM Repair 状态
* 最终 workflow 中的 API 列表

---

## 8. Benchmark 输出说明

benchmark 输出目录通常为：

```text
runs/benchmark_YYYYMMDD_HHMMSS/
```

### 8.1 总体输出文件

#### `benchmark_summary.json`

保存 benchmark 的核心汇总统计信息，包括：

* 总 case 数
* 通过 case 数
* parsing accuracy
* grounding accuracy
* parameter accuracy
* sequence accuracy
* executability rate
* pass rate
* precondition violation 数量
* repair 统计
* LLM repair 统计
* parser LLM 统计
* grounding LLM 统计
* case 结果摘要列表

#### `repair_debug.json`

保存 benchmark 中 repair 相关调试记录。

#### `summary_report.md`

保存 benchmark 的 Markdown 汇总报告。

### 8.2 单个 case 输出文件

每个 case 的输出目录下通常包括：

* `parsed_protocol.json`
* `grounded_workflow.json`
* `workflow.json`
* `validation_before.json`
* `validation_after.json`
* `repair_result.json`
* `execution_result.json`
* `case_result.json`
* `llm_*` 调试文件
* `workflow_before_llm_patch.json`
* `workflow_after_llm_patch.json`

其中 `case_result.json` 是单个测试用例最核心的摘要文件，通常包含：

* `case_id`
* `success`
* `checks`
* `details`
* `failure_reasons`

---

## 9. 配置文件说明

系统依赖以下配置文件：

* `configs/api_registry.yaml`
* `configs/initial_lab_state.yaml`
* `configs/benchmark_config.yaml`
* `configs/llm_parser_config.yaml`
* `configs/llm_grounder_config.yaml`
* `configs/llm_planner_config.yaml`
* `configs/llm_repair_config.yaml`
* `configs/llm_parser_notice.txt`
* `configs/llm_grounder_notice.txt`
* `configs/llm_planner_notice.txt`
* `configs/llm_repair_notice.txt`

各配置文件作用如下：

### `api_registry.yaml`

定义系统支持的 API 及其参数约束。

### `initial_lab_state.yaml`

定义虚拟实验室的初始状态。

### `benchmark_config.yaml`

定义 benchmark 的测试集目录、输出目录等参数。

### `llm_parser_config.yaml`

定义 parser 所使用的 LLM provider、model、temperature、timeout 等参数。

### `llm_grounder_config.yaml`

定义 grounder 所使用的 LLM 配置。

### `llm_planner_config.yaml`

定义 planner 所使用的 LLM 配置。

### `llm_repair_config.yaml`

定义 repair 所使用的 LLM 配置。

### `llm_*_notice.txt`

用于补充各阶段的提示信息或约束说明。

---

## 10. 环境依赖

项目至少依赖以下 Python 包：

* `typer`
* `pyyaml`
* `pydantic`

安装示例：

```bash
pip install typer pyyaml pydantic
```

若启用 LLM 路径，需要配置相应环境变量。

以 DeepSeek 为例：

```bash
export DEEPSEEK_API_KEY=your_key
```

Windows PowerShell：

```powershell
$env:DEEPSEEK_API_KEY="your_key"
```

---

## 11. 当前系统能力概述

当前系统已经具备以下完整能力链路：

* 协议解析
* API 映射
* operation 级编排
* workflow 合并规划
* 规则校验
* 自动修复
* 执行仿真
* benchmark 评测

项目整体上已经形成一个可运行、可调试、可评测、可扩展的实验协议自动化处理框架，可作为课程项目、原型系统或后续扩展开发的基础。


