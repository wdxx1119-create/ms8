"""Task templates for MS8 agent-native phase 1."""

INSTALL_TASK = """TASK_VERSION: 1
TASK ms8_first_install_onboarding
ROLE coding_agent
TARGET_MODEL 3B_coding_model+
SAFETY_LEVEL S1
PERMISSION user_confirmed_before_execution
REQUIRES terminal,python,pip
GOAL install MS8, learn MS8 built-in tools, verify installation, output report

RULES:
- ask_user_before_execution
- use_ms8_commands_only_after_install
- no_direct_file_edit
- no_database_edit
- no_memory_rewrite
- no_memory_delete
- no_sudo
- no_shell_rc_modify
- no_shadow_system_access
- no_upload_without_user_confirm
- stop_on_unknown

ALLOWED_COMMANDS:
- python --version
- python -m pip --version
- python -m ms8 version
- python -m pip install ms8
- python -m pip install .
- python -m ms8 agent init --profile DEFAULT_SAFE
- python -m ms8 agent init --profile TRUSTED_AGENT
- python -m ms8 agent task list
- python -m ms8 agent task show usage
- python -m ms8 agent task show ops
- python -m ms8 doctor
- python -m ms8 engine status --format text

PRECHECK:
RUN python --version -> python_version
RUN python -m pip --version -> pip_status
RUN python -m ms8 version -> ms8_version_check

DECISION:
- If [RUN_RESULT]ms8_version_check succeeded -> set [VAR]ms8_already_installed=true -> skip INSTALL step.
- If [RUN_RESULT]ms8_version_check failed -> set [VAR]ms8_already_installed=false -> execute INSTALL step.
- Do not fail the whole task only because ms8_version_check failed.

ASK_USER:
  "请选择权限模式：DEFAULT_SAFE（默认）或 TRUSTED_AGENT（需要用户确认）？"
  DEFAULT: DEFAULT_SAFE
  VALID_CHOICES: [DEFAULT_SAFE, TRUSTED_AGENT]
  -> permission_profile

DECISION:
- If user answered DEFAULT_SAFE -> CONTINUE with [VAR]permission_profile=DEFAULT_SAFE
- If user answered TRUSTED_AGENT -> REQUIRE explicit user confirmation, then CONTINUE with [VAR]permission_profile=TRUSTED_AGENT
- If user did not answer and interactive input is unavailable -> STOP NEEDS_CONFIRM
- If user input is invalid -> STOP NEEDS_CONFIRM

INSTALL:
RUN python -m pip install ms8 -> install_status

DECISION:
- If [RUN_RESULT]install_status failed and build backend/dependency is missing (for example setuptools.build_meta unavailable) -> status=FAIL and STOP.
- If [RUN_RESULT]install_status failed -> status=FAIL and STOP.
- If [RUN_RESULT]install_status succeeded -> CONTINUE ONBOARD.

ONBOARD:
RUN python -m ms8 agent init --profile <permission_profile> -> agent_init_status

LEARN_MS8:
RUN python -m ms8 agent task list -> task_list
RUN python -m ms8 agent task show usage -> usage_task
RUN python -m ms8 agent task show ops -> ops_task

VERIFY:
RUN python -m ms8 doctor -> doctor_status
RUN python -m ms8 engine status --format text -> ms8_status

OUTPUT MS8_FIRST_INSTALL_REPORT:
- status=PASS|FAIL|NEEDS_CONFIRM|ALREADY_INSTALLED
- permission_profile=
"""

OPS_TASK = """TASK_VERSION: 1
TASK ms8_daily_ops
ROLE local_ops_agent
TARGET_MODEL 3B_coding_model+
PERMISSION from MS8_HOME/agent_native/agent_policy.json
SAFETY_LEVEL S1
REQUIRES terminal,ms8
GOAL check MS8 health, preview repairs if needed, output report

IMPORTANT:
- Must execute STEP 1 -> STEP 2 -> STEP 3 in order.
- Do not skip any STEP.

RULES:
- use_ms8_commands_only
- no_direct_file_edit
- no_database_edit
- no_memory_rewrite
- no_memory_delete
- no_sudo
- no_shadow_system_access
- no_upload_without_user_confirm
- stop_on_unknown

STEP 1 - CHECK:
RUN python -m ms8 doctor -> doctor_status
RUN python -m ms8 engine status --format text -> ms8_status

STEP 2 - IF issue_found:
- issue_found=true if doctor output contains "Overall: degraded" or any line starting with " ⚠️ " or " ❌ ".
- Read permission_profile from MS8_HOME/agent_native/agent_policy.json.
- If permission_profile == TRUSTED_AGENT and issue_found=true:
    RUN python -m ms8 ops self-repair-run --mode dry-run -> repair_plan
- Else if issue_found=true:
    repair_plan="Upgrade to TRUSTED_AGENT to see repair preview."
- Else:
    repair_plan="No repair needed."

STEP 3 - IF critical_issue:
- critical_issue=true if doctor output contains "Overall: degraded" or "Overall: FAIL".
RUN python -m ms8 agent bug-report --bundle --redact -> bug_bundle
STOP output_summary_and_next_action
"""

CHECK_TASK = """TASK_VERSION: 1
TASK ms8_daily_check
ROLE local_ops_agent
TARGET_MODEL 3B_coding_model+
PERMISSION from MS8_HOME/agent_native/agent_policy.json
SAFETY_LEVEL S1
REQUIRES terminal,ms8
GOAL run health checks and produce repair plan hint

IMPORTANT:
- Must execute STEP 1 -> STEP 2 in order.
- Do not skip any STEP.

STEP 1 - CHECK:
RUN python -m ms8 doctor -> doctor_status
RUN python -m ms8 engine status --format text -> ms8_status

STEP 2 - DECIDE:
- issue_found=true if doctor output contains "Overall: degraded" or any line starting with " ⚠️ " or " ❌ ".
- Read permission_profile from MS8_HOME/agent_native/agent_policy.json.
- If permission_profile == TRUSTED_AGENT and issue_found=true:
    RUN python -m ms8 ops self-repair-run --mode dry-run -> repair_plan
- Else if issue_found=true:
    repair_plan="Upgrade to TRUSTED_AGENT to see repair preview."
- Else:
    repair_plan="No repair needed."
"""

REPORT_TASK = """TASK_VERSION: 1
TASK ms8_daily_report
ROLE local_ops_agent
TARGET_MODEL 3B_coding_model+
PERMISSION from MS8_HOME/agent_native/agent_policy.json
SAFETY_LEVEL S1
REQUIRES terminal,ms8
GOAL generate incident report and bug bundle when critical

STEP 1 - DETECT CRITICAL:
- critical_issue=true if doctor output contains "Overall: degraded" or "Overall: FAIL".

STEP 2 - REPORT:
- If critical_issue=true:
    RUN python -m ms8 agent bug-report --bundle --redact -> bug_bundle
- Else:
    bug_bundle="not_required"

OUTPUT MS8_OPS_REPORT:
- status=OK|WARN|FAIL
- doctor_status
- ms8_status
- repair_plan
- bug_bundle
- next_action
"""

USAGE_TASK = """TASK_VERSION: 1
TASK use_ms8_memory
ROLE ai_agent
SAFETY_LEVEL S1
PERMISSION from MS8_HOME/agent_native/agent_policy.json
GOAL use MS8 memory safely and usefully

RULES:
- retrieve_when_history_matters
- write_only_stable_facts
- do_not_store_secrets
- ask_before_sensitive_memory
- avoid_duplicate_memory
- prefer_current_user_instruction_over_old_memory
- do_not_rewrite_memory_without_user_confirm

WHEN task_depends_on_project_history:
RUN python -m ms8 ask "<topic>" -> memory_search_result
EXAMPLE: python -m ms8 ask "release process"

WHEN stable_user_preference_confirmed:
RUN python -m ms8 ask "记住: <summary>" -> memory_write_result
EXAMPLE: python -m ms8 ask "记住: 用户偏好中文日志输出"
"""

ABSORB_TASK = """TASK_VERSION: 1
TASK use_ms8_absorb
ROLE local_document_agent
TARGET_MODEL 3B_coding_model+
SAFETY_LEVEL S1
PERMISSION user_confirmed_before_execution
REQUIRES terminal,ms8
GOAL help the user safely connect local files to MS8 memory without learning commands

RULES:
- ask_user_before_execution
- use_ms8_commands_only
- no_direct_file_edit
- no_database_edit
- no_memory_rewrite
- no_memory_delete
- no_sudo
- no_shell_rc_modify
- no_shadow_system_access
- no_upload_without_user_confirm
- stop_on_unknown

ALLOWED_COMMANDS:
- python -m ms8 agent run absorb --mode status
- python -m ms8 agent run absorb --mode setup --path <directory> --confirm
- python -m ms8 agent run absorb --mode search --query <query>
- python -m ms8 agent run absorb --mode review
- python -m ms8 absorb status
- python -m ms8 absorb add <directory>
- python -m ms8 absorb rescan
- python -m ms8 absorb ingest
- python -m ms8 absorb search <query> --pretty
- python -m ms8 absorb review list

ASK_USER:
  "你要让 MS8 读取哪个本地文件夹？我会先检查并进入待审，不会默认自动写入长期记忆。是否继续？"
  REQUIRED_FOR: setup
  -> user_authorized_directory

FLOW:
1. If user asks to check absorb status:
   RUN python -m ms8 agent run absorb --mode status -> absorb_status
2. If user asks to connect a folder:
   ASK_USER first.
   If user did not confirm -> STOP NEEDS_CONFIRM.
   RUN python -m ms8 agent run absorb --mode setup --path <directory> --confirm -> absorb_setup
3. If user asks to search absorbed files:
   RUN python -m ms8 agent run absorb --mode search --query <query> -> absorb_search
4. If user asks to review pending absorbed chunks:
   RUN python -m ms8 agent run absorb --mode review -> absorb_review

OUTPUT MS8_AGENT_RESULT:
- action=absorb
- mode=status|setup|search|review
- status=PASS|FAIL|NEEDS_CONFIRM
- executed_commands=
- summary=
- next_action=

SAFETY_NOTES:
- Default mode is read-only/status/search/review listing.
- Folder setup requires explicit user confirmation.
- No autosubmit/apply command is allowed by this task.
- No raw file content is printed unless the user explicitly searches and reviews previews.
"""

README_AGENT = """# MS8 Agent-native Onboarding

Agent executes. MS8 controls. User authorizes.

这是给小模型和 Agent 的最小操作手册：先检查，再执行，最后汇报。

## 安全边界

1. Agent 只能调用 MS8 内置命令。
2. Agent 不能碰影子系统。
3. Agent 不能改用户数据。

## 权限模式

- DEFAULT_SAFE（默认）：只检查、初始化、读取状态、生成脱敏报告。
- TRUSTED_AGENT：可以做 repair dry-run 预览，但仍不执行真实修复。

## 快速开始

1. `python -m ms8 agent init --profile DEFAULT_SAFE`
2. `python -m ms8 agent task show install`
3. `python -m ms8 agent task show usage`
4. `python -m ms8 agent task show absorb`
5. `python -m ms8 agent task show check`
6. `python -m ms8 agent task show report`
7. `python -m ms8 doctor`

## 安装环境前置

- 需要可用的 Python 与 pip。
- 首次安装路径需要可写。
- 如果走源码安装路径，构建后端依赖（例如 setuptools.build_meta）必须可用。
- 若依赖不可用，请先修复 Python/pip 构建环境，再执行安装任务。

策略路径：
`MS8_HOME/agent_native/agent_policy.json`

## 策略说明

权限策略 `MS8_HOME/agent_native/agent_policy.json` 是用户级全局文件，所有项目共享。

这意味着：

- 在任意项目中查看到的是同一份 Agent 权限策略。
- 切换权限模式会影响所有项目。
- 项目目录 `.ms8/agent_native/` 只保存任务模板，不保存用户授权策略。
- 如果只想移除当前项目的 Agent-native 接入文件，请运行 `python -m ms8 agent remove`。
- `agent remove` 默认不会删除全局权限策略。

说明：没有独立 bug_report.task，故障上报逻辑集成在 ops.task 的 STEP 3。
"""
