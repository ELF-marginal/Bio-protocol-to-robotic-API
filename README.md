


# Bio-Protocol 

## 项目简介

Bio-Protocol  是一个面向虚拟实验室场景的实验协议执行原型系统。系统接收自然语言形式的实验协议文本，将其逐步转换为结构化步骤与可执行 API 工作流，并在执行前后进行校验、修复与状态追踪，最终输出完整的调试文件、执行结果文件和汇总报告。项目主入口由 `main.py` 提供，支持单次运行与批量评测两种模式。:

系统整体流程为：

协议文本输入 → 协议解析 → API Grounding → 工作流补全规划 → 规则校验 → 自动修复 / LLM 修复 → 虚拟执行 → 结果落盘。

---

## 系统特点

### 1. 支持规则链路与 LLM 链路并存

项目同时保留了规则版与 LLM 版两套核心后端。未开启 LLM 时，系统使用规则解析与规则 grounding；开启后可切换为 LLM 主解析和 LLM 主 grounding，并在失败时回退到规则流程。

### 2. 具备显式验证与修复机制

系统不会把生成的工作流直接交给执行器，而是先通过 Validator 检查 API 合法性、参数缺失、调用顺序、前置条件等问题；若存在问题，则进入规则修复阶段，必要时再进入 LLM 修复阶段。

### 3. 运行过程可追踪

无论是单次运行还是 benchmark，系统都会将解析结果、grounding 结果、修复结果、执行结果和汇总报告全部保存到输出目录，便于调试、复现实验和展示系统完整工作流程。

### 4. 面向实验室 API 的工作流执行

系统以实验室 API 为执行目标，通过虚拟状态机模拟冰箱、机械臂、试管、移液器、加热器等设备操作，为后续接入真实硬件控制接口提供统一的软件骨架。

---

## 项目结构

```text
project_root/
├─ main.py
├─ src/
│  ├─ models/
│  │  └─ contracts.py
│  ├─ pipeline/
│  │  ├─ benchmark_runner.py
│  │  ├─ executor.py
│  │  ├─ llm_grounder.py
│  │  ├─ llm_parser.py
│  │  ├─ llm_repair.py
│  │  ├─ mock_grounding.py
│  │  ├─ mock_parser.py
│  │  ├─ repair.py
│  │  ├─ unit_normalizer.py
│  │  ├─ validator.py
│  │  └─ workflow_planner.py
│  └─ utils/
│     └─ io.py
├─ configs/
│  ├─ api_registry.yaml
│  ├─ initial_lab_state.yaml
│  ├─ llm_parser_config.yaml
│  ├─ llm_grounding_config.yaml
│  ├─ llm_repair_config.yaml
│  ├─ llm_grounder_notice.txt
│  └─ benchmark_config.yaml
├─ tests/
│  └─ cases/
│     └─ *.yaml
└─ runs/
   ├─ wf_xxx/
   └─ benchmark_YYYYMMDD_HHMMSS/
````

`main.py` 是命令行主入口；`src/pipeline/` 存放协议解析、grounding、规划、校验、修复、执行和 benchmark 逻辑；`configs/` 用于存放 API 注册表、实验室初始状态与 LLM 配置；`tests/cases/` 用于保存 benchmark 测试样例；`runs/` 用于保存运行结果。  

---

## 核心模块说明

### `main.py`

项目 CLI 主入口，基于 `typer` 构建，提供 `run`、`version` 和 `benchmark` 三个命令。该文件负责把各个 pipeline 模块串联起来，并在运行结束后将中间结果与执行结果写入 `runs/` 目录。  

### `src/pipeline/mock_parser.py`

规则版协议解析器。它通过句子切分和关键词匹配识别 `take`、`add`、`mix`、`incubate` 等动作，并抽取实体与参数，如来源、目标、体积、温度和时间。

### `src/pipeline/llm_parser.py`

LLM 版协议解析模块。它先对原始文本执行标准化预处理，包括换行清理、单位归一化、长度裁剪等，再构造 LLM 输入，要求模型严格输出结构化 JSON。若未开启 LLM，则回退到规则解析器。模块还提供解析质量评估逻辑，用于识别缺失字段、低置信度步骤、未知动作和复杂句。  

### `src/pipeline/mock_grounding.py`

规则版 grounding 模块。它将解析后的步骤映射成 API 工作流。例如：

* `take` 映射为 `fridge.open → robot.pick → robot.place → fridge.close`
* `add` 映射为 `pipette.transfer`
* `incubate` 映射为 `heater.set_temperature → heater.place → timer.wait → heater.remove`
* `mix` 映射为 `pipette.mix`。

### `src/pipeline/llm_grounder.py`

LLM 版 grounding 模块。它将解析结果、API 注册表和实验室状态打包后交给 LLM，将结构化步骤转换为 API 工作流。模块会检查 LLM 输出是否满足 JSON 与 schema 要求，并额外验证是否包含未注册 API。若关闭 LLM grounding，则直接使用规则版 grounding。  

### `src/pipeline/workflow_planner.py`

工作流补全模块。它会在现有 API 工作流基础上自动补充执行安全所需的前后置操作。例如在移液和混匀前插入 `tube.uncap`、`pipette.attach_tip`，在流程结束后自动补 `pipette.discard_tip` 和 `tube.cap`。

### `src/pipeline/validator.py`

规则校验模块。它会对工作流进行逐条检查，包括：

* API 是否存在于注册表中
* 必需参数是否缺失
* 顺序是否正确
* 移液前是否已装枪头
* 目标试管是否已开盖
* 加热前是否已设置温度等。
  校验结果会输出 `valid`、`issue_count` 和问题明细列表。

### `src/pipeline/repair.py`

规则修复模块。它根据 Validator 输出的问题类型自动插入修复调用，例如补充 `pipette.attach_tip`、`tube.uncap`、`fridge.open` 或 `heater.set_temperature`，并重新编号工作流。

### `src/pipeline/llm_repair.py`

LLM 修复模块。它只在规则修复后仍存在残留问题时触发，并且仅针对允许的问题类型进行局部最小补丁式修复，而不是直接重写整条工作流。 

### `src/pipeline/executor.py`

虚拟执行器。它从实验室初始状态出发，顺序执行 API 调用，记录执行事件、最终状态以及每一步执行后的状态快照；如中途出现前置条件或参数错误，则立即停止并返回失败结果。

### `src/pipeline/benchmark_runner.py`

批量评测模块。它会遍历 `tests/cases/` 下的 YAML 用例，对每个 case 执行完整流水线，并统计解析准确率、grounding 准确率、参数准确率、序列准确率、可执行率、通过率等指标，同时输出每个 case 的详细结果文件和总体汇总报告。 

### `src/pipeline/unit_normalizer.py`

单位归一化工具模块，用于将协议文本中的 `uL`、`μL`、`mL` 等体积单位统一换算到 `uL`。规则解析器会调用它来规范化体积参数。

---

## CLI 命令说明

项目 CLI 使用 `typer` 实现，当前包含以下命令。 

### 1. 查看版本

```bash
python main.py version
```

输出：

```bash
bio-protocol v1.0
```



### 2. 单次运行协议

```bash
python main.py run --text "Take the sample tube from the fridge. Add 0.1 mL buffer to the sample tube. Incubate at 37C for 10 min."
```

或

```bash
python main.py run --file test.txt
```

`run` 命令参数如下：

* `--text`：直接输入原始协议文本
* `--file`：输入协议文件路径，仅支持 `.txt` 和 `.md`
* `--title`：协议标题，默认值为 `MVP Demo Protocol`
* `--enable-validator`：是否在执行前启用规则校验，默认 `True`
* `--enable-repair`：校验失败后是否尝试规则修复，默认 `True`
* `--enable-llm-repair`：是否在规则修复后启用 LLM 修复，默认 `False`
* `--enable-llm-parser`：是否启用 LLM 主解析，默认 `False`
* `--enable-llm-grounding`：是否启用 LLM 主 grounding，默认 `False`

注意：`--text` 与 `--file` 必须二选一，不能同时提供，也不能同时省略。

### 3. 批量评测

```bash
python main.py benchmark
```

或指定测试目录：

```bash
python main.py benchmark --cases-dir tests/cases
```

`benchmark` 命令参数如下：

* `--cases-dir`：测试用例目录，目录中应包含若干 `.yaml` 文件
* `--config`：benchmark 配置文件路径，默认 `configs/benchmark_config.yaml`
* `--enable-llm-repair`：是否在 benchmark 流程中启用 LLM 修复，默认 `False`
* `--enable-llm-parser`：是否在 benchmark 流程中启用 LLM 主解析，默认 `False`
* `--enable-llm-grounding`：是否在 benchmark 流程中启用 LLM 主 grounding，默认 `False`

如果 `--cases-dir` 未指定，则会优先从配置文件中读取；输出目录默认基于配置中的 `output_root`，并自动生成形如 `benchmark_YYYYMMDD_HHMMSS` 的时间戳目录。

---

## 运行流程说明

### 单次运行 `run`

单次运行的处理流程为：

1. 读取协议文本
2. 执行文本预处理
3. 进行协议解析
4. 进行 grounding 生成基础 API 工作流
5. 执行工作流补全
6. 执行规则校验
7. 如有必要，执行规则修复
8. 如仍有残留问题，可执行 LLM 修复
9. 运行虚拟执行器
10. 将全部中间结果和最终结果写入 `runs/<workflow_id>/` 目录。 

### 批量运行 `benchmark`

批量评测会对每个 case 独立执行与 `run` 类似的完整流水线，并额外将实际结果与期望结果进行比对，统计：

* 解析准确率
* grounding 准确率
* 参数准确率
* 序列准确率
* 可执行率
* 通过率
  最后将汇总统计写入 benchmark 输出目录。  

---

## `run` 输出文件说明

单次运行结束后，程序会在 `runs/<workflow_id>/` 下生成一组结果文件。

### 输入与解析阶段

#### `protocol_input.json`

保存原始协议输入信息，包括协议 ID、标题、输入来源和原始文本内容。

#### `parser_preprocess.json`

保存解析前的文本预处理结果，包括标准化后的文本、单位替换次数、是否截断、处理前后长度等信息。

#### `llm_parser_result.json`

保存解析阶段的后端状态，例如是否启用了 LLM、是否发生回退、JSON 是否有效、失败原因等。

#### `llm_parser_input.json`

当启用 LLM 解析时，保存发给模型的输入内容。

#### `llm_parser_raw_output.json`

当启用 LLM 解析时，保存模型返回的原始输出。

#### `llm_parser_parsed_output.json`

当 LLM 输出成功解析后，保存结构化解析结果。

#### `parsed_protocol.json`

保存最终采用的结构化协议步骤，是 grounding 阶段的直接输入。

### Grounding 与工作流阶段

#### `grounding_result.json`

保存 grounding 阶段的总体结果，包括后端模式、是否有效、是否包含未注册 API、失败原因等。

#### `grounding_validation_result.json`

保存 grounding 输出的合法性检查结果，例如工作流结构是否合法、未注册 API 检查是否一致。

#### `llm_grounding_input.json`

当启用 LLM grounding 时，保存发给模型的 grounding 输入。

#### `llm_grounding_raw_output.json`

保存 grounding 模型返回的原始输出。

#### `llm_grounding_parsed_output.json`

保存成功解析后的 grounding 结构化输出。

#### `grounded_workflow.json`

保存 grounding 阶段直接生成的基础工作流。

#### `workflow.json`

保存最终参与执行的工作流，通常已经过规划补全、校验与修复。

### 校验与修复阶段

#### `validation_before_rule_repair.json`

保存规则修复前的校验结果。

#### `validation_result.json`

保存最终校验结果。

#### `repair_result.json`

保存规则修复结果，包括是否修复成功、插入了哪些修复调用。

#### `llm_input.json`

当触发 LLM 修复时，保存发给修复模型的输入。

#### `llm_raw_output.json`

保存修复模型的原始输出。

#### `llm_parsed_output.json`

保存成功解析后的 LLM 修复操作序列。

#### `workflow_before_llm_patch.json`

保存应用 LLM 补丁前的工作流。

#### `workflow_after_llm_patch.json`

保存应用 LLM 补丁后的工作流。

#### `llm_patch_result.json`

保存 LLM 修复是否调用、补丁是否应用、是否被接受、失败原因等摘要信息。

#### `llm_validation_result.json`

保存 LLM 修复前后剩余问题数量的变化情况。

### 执行阶段

#### `execution_result.json`

保存执行器的总体执行结果，包括是否成功、实际执行 API 数量、执行事件等。

#### `final_state.json`

保存执行结束后的实验室最终状态。

#### `state_snapshots.json`

保存每一步 API 执行后的实验室状态快照，用于逐步追踪状态变化。

### 摘要报告

#### `summary_report.md`

保存单次运行的 Markdown 摘要报告。内容包括：

* Protocol ID
* Workflow ID
* Executed Calls
* Success
* State Snapshots
* Validation Valid
* Validation Issues
* Repaired
* Parser Backend
* Parser Fallback Used
* Parser Failure Reason
* Grounding Backend
* Grounding Valid
* Grounding Failure Reason
* Contains Unregistered API
* Unregistered APIs
* LLM Repair Invoked
* LLM Patch Accepted
* LLM Failure Reason
* 最终 API 列表。

---

## `benchmark` 输出文件说明

批量评测结束后，程序会在 `runs/benchmark_<timestamp>/` 下生成总体汇总文件，并为每个测试用例创建单独子目录。 

### Benchmark 根目录文件

#### `benchmark_summary.json`

保存整轮 benchmark 的总体统计信息，包括：

* `total_cases`
* `passed_cases`
* `parsing_accuracy`
* `grounding_accuracy`
* `parameter_accuracy`
* `sequence_accuracy`
* `executability_rate`
* `pass_rate`
  以及修复、LLM 修复、Parser LLM、Grounding LLM 等统计结果。

#### `repair_debug.json`

保存每个 case 的修复调试记录，包括修复前后问题数、应用的修复操作、是否调用 LLM 修复、是否接受补丁、解析失败原因等。

#### `summary_report.md`

保存整轮 benchmark 的 Markdown 汇总报告，用于快速查看整体性能指标与结果摘要。

### 每个 case 子目录文件

每个 case 对应一个子目录，通常包含：

* `parser_preprocess.json`
* `llm_parser_result.json`
* `parsed_protocol.json`
* `grounded_workflow.json`
* `workflow_before_repair.json`
* `validation_before.json`
* `repair_result.json`
* `validation_after.json`
* `workflow.json`
* `llm_input.json`
* `llm_parser_input.json`
* `llm_parser_raw_output.json`
* `llm_parser_parsed_output.json`
* `grounding_result.json`
* `grounding_validation_result.json`
* `llm_grounding_input.json`
* `llm_grounding_raw_output.json`
* `llm_grounding_parsed_output.json`
* `llm_raw_output.json`
* `llm_parsed_output.json`
* `workflow_before_llm_patch.json`
* `workflow_after_llm_patch.json`
* `llm_patch_result.json`
* `llm_validation_result.json`
* `execution_result.json`
* `case_result.json`

其中 `case_result.json` 是单个测试用例最重要的汇总文件，保存该 case 的成功状态、检查项、失败原因和详细评测结果。

---

## Benchmark 评测指标说明

系统在 benchmark 中会对每个测试用例进行多维度评估，常见指标包括：

* **Parsing Accuracy**：解析结果与期望步骤的一致程度
* **Grounding Accuracy**：生成 API 动作与期望动作的一致程度
* **Parameter Accuracy**：参数填充正确率
* **Sequence Accuracy**：API 调用顺序正确率
* **Executability Rate**：工作流可成功执行的比例
* **Pass Rate**：综合通过率

此外，系统还会额外统计前置条件违规次数、规则修复数量、LLM 修复调用次数、Parser LLM 成功率以及 Grounding LLM 成功率。 

---

## 示例

### 单次运行

```bash
python main.py run \
  --text "Take the sample tube from the fridge. Add 0.1 mL buffer to the sample tube. Incubate at 37C for 10 min." \
  --title "Incubation Demo" \
  --enable-validator True \
  --enable-repair True
```

### 启用 LLM 解析与 LLM grounding

```bash
python main.py run \
  --file test.txt \
  --enable-llm-parser True \
  --enable-llm-grounding True
```

### 启用 benchmark

```bash
python main.py benchmark --cases-dir tests/cases
```

---

## 当前支持的典型动作

从当前规则解析和规则 grounding 逻辑来看，系统已支持以下典型实验动作：

* 取样本：`take`
* 加液：`add`
* 混匀：`mix`
* 孵育：`incubate`

这些动作会被进一步映射为实验室 API，例如：

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
* `timer.wait`。  

---

## 适用场景

本项目适用于以下场景：

1. 生物实验协议到实验室 API 的映射研究
2. LLM 在实验任务理解、结构化解析和工具调用中的验证
3. 面向实验室自动化的工作流执行与状态模拟
4. 协议解析、Grounding、修复和评测一体化原型搭建

---

## 版本

当前 CLI 版本号为：

```text
bio-protocol v1.0
```

