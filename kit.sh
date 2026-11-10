#!/usr/bin/env bash
# 流程管家：看门人。记住做哪个产品、第几阶段；给出本阶段任务；自检没过不让翻页。
# 不碰模型、不花钱、不存密钥。规则仍在 AGENTS.md 和手册里，管家只保证"该交的交了、该停的停了"。
#
# 主路线（AI 在对话里调用，产品经理只管聊天）：
#   bash kit.sh 任务 [产品名]              给出本阶段任务（写到 docs/本阶段任务.md 并打印）
#   bash kit.sh 自检 [产品名]              跑 verify.sh，结果记入状态；退出码 0 = 过
#   bash kit.sh 通过 [产品名] [下一阶段]    产品经理在对话里说"验收通过"后才能调；自检没过会被拒绝
#   bash kit.sh 状态 [产品名]              现在到哪了
#   bash kit.sh 回到 [产品名] 阶段X         管家记的阶段和实际不符时手动校正
# 副路线（产品经理双击 开工.command）：
#   bash kit.sh                            一个窗口带着走：打开编码工具、关掉后自检、问验收
#   bash kit.sh 换工具 [产品名]             下次双击重新选编码工具
# 只有一个产品时产品名可省略。
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

say()  { printf '%s\n' "$*"; }
line() { say "────────────────────────────────────────────────────────"; }
ask()  { # 交互模式用。ask "问题" → ANS
  printf '%s ' "$1"
  if ! IFS= read -r ANS; then say; say "没有收到输入，先到这里。下次双击开工继续。"; exit 1; fi
  ANS="$(printf '%s' "$ANS" | tr -d '[:space:]')"
}
ask_yn() { while :; do ask "$1 [y/n]"; case "$ANS" in y|Y|是) return 0;; n|N|否) return 1;; esac; say "请输入 y 或 n。"; done; }

# ---------- 参数：子命令 [产品名] [其他] ----------
CMD="${1:-}"; shift || true
PRODUCTS=()
for d in projects/*/; do [ -d "$d" ] && PRODUCTS+=("$(basename "$d")"); done
if [ "${#PRODUCTS[@]}" -eq 0 ]; then say "projects/ 下没有产品文件夹。先按 README 放好 PRD，或让 AI 新建产品。"; exit 2; fi
PRODUCT=""
if [ $# -ge 1 ] && [ -d "projects/$1" ]; then PRODUCT="$1"; shift
elif [ "${#PRODUCTS[@]}" -eq 1 ]; then PRODUCT="${PRODUCTS[0]}"
elif [ -n "$CMD" ]; then
  say "有多个产品，请指明：bash kit.sh ${CMD} <产品名> ...   现有：${PRODUCTS[*]}"; exit 2
else
  say "有多个产品，这次做哪个？"; i=1
  for p in "${PRODUCTS[@]}"; do
    st="还没开始"; [ -f "projects/$p/docs/流程状态.txt" ] && st="$(grep -m1 '^阶段=' "projects/$p/docs/流程状态.txt" | cut -d= -f2-)"
    say "  $i) ${p}（${st}）"; i=$((i+1))
  done
  while :; do ask "输入编号："; if [[ "$ANS" =~ ^[0-9]+$ ]] && [ "$ANS" -ge 1 ] && [ "$ANS" -le "${#PRODUCTS[@]}" ]; then PRODUCT="${PRODUCTS[$((ANS-1))]}"; break; fi; done
fi
P="projects/$PRODUCT"
STATE="$P/docs/流程状态.txt"
HIST="$P/docs/流程历史.txt"
TASK="$P/docs/本阶段任务.md"
mkdir -p "$P/docs"

# ---------- 状态文件（key=value） ----------
sget() { [ -f "$STATE" ] && grep -m1 "^$1=" "$STATE" | cut -d= -f2- || true; }
sset() { local t; t="$(mktemp)"; { [ -f "$STATE" ] && grep -v "^$1=" "$STATE"; printf '%s=%s\n' "$1" "$2"; } > "$t" 2>/dev/null; mv "$t" "$STATE"; }
hist() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M')" "$*" >> "$HIST"; }
valid_stage() { [[ "$1" =~ ^(阶段0|阶段1|阶段2-[1-9][0-9]*|阶段3-[0-9]+|阶段4|阶段5|完成)$ ]]; }

if [ ! -f "$STATE" ]; then
  if [ -f "$P/docs/项目状态.md" ] && [ -z "$CMD" ]; then
    say "这个产品以前做过，但管家还没记录过它。现在做到第几阶段？"
    say "  可填：阶段0、阶段1、阶段2-1、阶段2-2…、阶段3-0、阶段3-1…、阶段4、阶段5（直接回车 = 阶段0）"
    ask "阶段："; [ -z "$ANS" ] && ANS="阶段0"; valid_stage "$ANS" || ANS="阶段0"
    sset 阶段 "$ANS"
  else
    sset 阶段 阶段0
  fi
  sset 状态 待开工; hist "管家开始记录，起点 $(sget 阶段)"
  [ -f "$P/docs/项目状态.md" ] && [ -n "$CMD" ] && say "提示：这个产品以前做过但管家刚开始记录，起点按 阶段0。实际不是的话用：bash kit.sh 回到 ${PRODUCT} 阶段X"
fi

# ---------- 阶段信息（来自 AGENTS.md 第 7 节加载表） ----------
stage_info() { # 设置 READ OUT PM PASS NOCODE
  NOCODE=0
  case "$1" in
    阶段0) READ="AGENTS.md；docs/PRD/；通用手册第二节与《二之一、PRD 体检》"
           OUT="docs/阶段文档/阶段0-PRD补全清单.md；docs/evidence/阶段0/是否表.md；docs/项目状态.md（新建或更新）"
           PM="回答\"必须问\"的问题（一次不超过 5 个，可回复\"按推荐\"）"
           PASS="《PRD 补全清单》全部关闭"; NOCODE=1 ;;
    阶段1) READ="通用手册第一部分（一至六节）"
           OUT="docs/阶段文档/技术适配声明.md（含第 7 节子阶段规划）；决策台账初版；docs/evidence/阶段1/是否表.md"
           PM="确认三件事：理解和子阶段规划对不对 / 有没有新增费用·平台·范围 / 暂缓项影不影响验收"
           PASS="台账无影响本阶段的待定项"; NOCODE=1 ;;
    阶段2-*) READ="通用手册第二、三部分"
           OUT="docs/阶段文档/$1技术开发文档.md；代码；产品 README.md；证据包 docs/evidence/$1/（截图、照着做清单、是否表、verify-输出、冒烟记录）"
           PM="提供模型 Key；按\"照着做\"清单验收；对真实样例打分"
           PASS="证据包已交 + 是否表全\"是\"" ;;
    阶段3-0) READ="前端手册全文"
           OUT="docs/阶段文档/前端适配声明.md；docs/evidence/阶段3-0/是否表.md"
           PM="给视觉方向（或说\"中性风格\"）；确认前端适配声明"
           PASS="声明经产品经理确认"; NOCODE=1 ;;
    阶段3-*) READ="前端手册全文（重点第 22 节本子阶段对应部分）"
           OUT="docs/阶段文档/$1技术开发文档.md；前端代码；产品 README.md；证据包 docs/evidence/$1/"
           PM="看代表页反馈；用手机走一遍"
           PASS="证据包已交 + 是否表全\"是\"" ;;
    阶段4) READ="部署手册全文"
           OUT="docs/阶段文档/上线方案摘要.md（含费用与耗时估算）；部署；产品 README.md；证据包 docs/evidence/阶段4/"
           PM="注册腾讯云并实名、浏览器授权命令行登录、开通云开发环境并启用云托管、创建服务端 API Key 并点复制、确认费用、按验收清单验证"
           PASS="部署手册第六部分验收清单全过" ;;
    阶段5) READ="部署手册第五部分\"收尾\""
           OUT="产品 README.md；docs/项目状态.md 记录完成；退出本机命令行登录；服务端 API Key 到期提醒"
           PM="不删服务端 API Key；确认 AI 退出命令行登录后线上仍正常"
           PASS="状态文件记录完成"; NOCODE=1 ;;
    *) READ="（未知阶段）"; OUT=""; PM=""; PASS="" ;;
  esac
}

# 下一阶段的候选：唯一时 NEXT_FIXED 有值；有分岔时 NEXT_OPTS 列出可选项
next_options() {
  local cur="$1" n; NEXT_FIXED=""; NEXT_OPTS=""
  case "$cur" in
    阶段0) NEXT_FIXED=阶段1 ;;
    阶段1) NEXT_FIXED=阶段2-1 ;;
    阶段2-*) n="${cur#阶段2-}"; NEXT_OPTS="阶段2-$((n+1)) 阶段3-0" ;;
    阶段3-0) NEXT_FIXED=阶段3-1 ;;
    阶段3-*) n="${cur#阶段3-}"; NEXT_OPTS="阶段3-$((n+1)) 阶段4 阶段5" ;;
    阶段4) NEXT_FIXED=阶段5 ;;
    阶段5) NEXT_FIXED=完成 ;;
    *) NEXT_FIXED=完成 ;;
  esac
}
explain_options() { # 用产品语言解释分岔
  case "$1" in
    阶段2-*) say "  阶段2-$(( ${1#阶段2-} + 1 ))：阶段 2 还有下一个子阶段（看《技术适配声明》第 7 节子阶段规划）"; say "  阶段3-0：阶段 2 做完了，进入正式前端" ;;
    阶段3-*) say "  阶段3-$(( ${1#阶段3-} + 1 ))：阶段 3 还有下一个子阶段"; say "  阶段4：上线（要注册腾讯云）"; say "  阶段5：这次做到合格为止，直接收尾" ;;
  esac
}

# ---------- 本阶段任务文件 ----------
write_task() { # $1 阶段 $2 模式 chat|window
  local st="$1" mode="$2" n lastfail=""
  stage_info "$st"
  n="$(sget 开工次数)"; n=$(( ${n:-0} + 1 )); sset 开工次数 "$n"
  if [ "$(sget 状态)" = "自检没过" ] && [ -f "$P/docs/evidence/$st/verify-输出.txt" ]; then
    lastfail="$( { grep -E '^(缺：|问题：|放错位置：)' "$P/docs/evidence/$st/verify-输出.txt"; sed -n '/汇总/,$p' "$P/docs/evidence/$st/verify-输出.txt" | grep -E '不通过|未配置|待验'; } | sort -u || true)"
  fi
  {
    say "# 本阶段任务（流程管家自动生成，每次开工覆盖；AI 不要改这个文件）"
    say
    say "- 产品：${PRODUCT}（文件夹 projects/${PRODUCT}/）"
    say "- 阶段：${st}（本阶段第 ${n} 次开工）"
    say "- 生成时间：$(date '+%Y-%m-%d %H:%M')"
    say
    say "## 先做"
    say "1. 读工具包根目录的 AGENTS.md，按第 0 节启动流程处理这个产品（有 docs/项目状态.md 就是老项目，从它记录的地方接着做）。"
    say "2. 本阶段只读这些手册内容：${READ}。读完按 AGENTS.md 第 7 节做一次对账（列出与十条底线冲突的句子，没有就说\"已对账，无冲突\"）。"
    say
    say "## 本阶段要交的"
    say "${OUT}"
    say "证据包的固定文件见 AGENTS.md 第 5 节。"
    say
    say "## 产品经理这阶段要做的（由你翻译成\"去哪、点什么、填什么、看到什么算对\"）"
    say "${PM}"
    say
    say "## 通过条件"
    say "${PASS}"
    if [ -n "$lastfail" ]; then
      say
      say "## 上次自检没过的项（先解决这些）"
      say '```'
      say "$lastfail"
      say '```'
    fi
    say
    say "## 规矩"
    say "- 只做 ${st}，不做下一阶段的事。$( [ "$NOCODE" -eq 1 ] && say "这是无代码阶段，不要写代码、不要建 backend/ frontend/。" )"
    say "- 需要产品经理决定的事，用决策卡当面问他（一次不超过 5 个），记入 docs/项目状态.md。"
    say "- 做完后在工具包根目录运行 \`bash kit.sh 自检 ${PRODUCT}\`（它会跑 verify.sh 并记录结果），把六项结果抄进是否表。"
    say "- 不要修改 docs/本阶段任务.md、docs/流程状态.txt、docs/流程历史.txt，它们归流程管家。"
    if [ "$mode" = "chat" ]; then
      say "- 自检通过后，把 docs/evidence/${st}/照着做清单.md 的内容用产品语言讲给产品经理，请他验收；他明确说\"验收通过\"后，才运行 \`bash kit.sh 通过 ${PRODUCT}\`（有分岔时按它提示带上下一阶段名）。他没说通过，就不要调。"
    else
      say "- 全部做完后对产品经理说这句话：\"本阶段做完了，请关闭我，回到开工窗口看自检结果。\""
    fi
  } > "$TASK"
}

run_verify() { # 跑自检并记状态；返回 verify 退出码
  local st="$1" rc
  bash verify.sh "$PRODUCT" "$st"; rc=$?
  if [ "$rc" -eq 0 ]; then sset 状态 等验收; hist "${st} 自检通过"; else sset 状态 自检没过; hist "${st} 自检没过"; fi
  return "$rc"
}

advance() { # $1 当前 $2 下一阶段
  hist "$1 验收通过 → $2"; sset 阶段 "$2"; sset 状态 待开工; sset 开工次数 0
}

show_status() {
  local st status
  st="$(sget 阶段)"; status="$(sget 状态)"; stage_info "$st"
  line; say "产品：${PRODUCT}"; say "阶段：${st}　　状态：${status}"
  [ -n "$(sget 工具)" ] && say "编码工具：$(tool_name "$(sget 工具)")"
  [ "$st" != "完成" ] && { say "本阶段要交：${OUT}"; say "本阶段你要做：${PM}"; }
  line
}

# ---------- 编码工具（只有双击路线用） ----------
tool_name() { case "$1" in
  claude) echo "Claude Code";; codex) echo "Codex";; pi) echo "Pi";; gemini) echo "Gemini CLI";; opencode) echo "OpenCode";;
  cursor) echo "Cursor 命令行版";; kimi) echo "Kimi Code";; mimo) echo "MiMo Code";; zcode) echo "Z Code";;
  other) echo "其他工具（Cursor、Windsurf、Trae 等带界面的）";; *) echo "$1";; esac; }
tool_mode() { case "$1" in claude|codex|pi|gemini|opencode|cursor) echo auto;; kimi|mimo|zcode) echo semi;; *) echo manual;; esac; }
tool_bin()  { case "$1" in cursor) command -v cursor-agent >/dev/null 2>&1 && echo cursor-agent || echo agent;; *) echo "$1";; esac; }
detect_tools() { FOUND=(); for t in claude codex pi gemini opencode cursor kimi mimo zcode; do command -v "$(tool_bin "$t")" >/dev/null 2>&1 && FOUND+=("$t"); done; }
choose_tool() {
  detect_tools
  if [ "${#FOUND[@]}" -eq 0 ]; then say "电脑上没找到我认识的编码工具，用通用模式。"; TOOL=other; return; fi
  say "电脑上装了这些编码工具："; i=1
  for t in "${FOUND[@]}"; do say "  $i) $(tool_name "$t")"; i=$((i+1)); done
  say "  $i) $(tool_name other)"
  while :; do ask "用哪个？输入编号（直接回车 = 1）："; [ -z "$ANS" ] && ANS=1
    if [[ "$ANS" =~ ^[0-9]+$ ]] && [ "$ANS" -ge 1 ] && [ "$ANS" -le "$i" ]; then
      if [ "$ANS" -eq "$i" ]; then TOOL=other; else TOOL="${FOUND[$((ANS-1))]}"; fi; break
    fi
  done
}
copy_clip() { if command -v pbcopy >/dev/null 2>&1; then printf '%s' "$1" | pbcopy; return 0; fi; return 1; }
launch_tool() { # $1 工具 $2 开场白
  local t="$1" prompt="$2" mode bin
  mode="$(tool_mode "$t")"; bin="$(tool_bin "$t")"
  case "$mode" in
    auto)
      say "正在打开 $(tool_name "$t")，开场白已经贴好。像平时一样和它对话；它说\"本阶段做完了\"你就关掉它（输入 /exit 或按两次 Ctrl+C），回到这个窗口。"
      line
      case "$t" in
        claude|codex|pi|cursor) "$bin" "$prompt" ;;
        gemini)   "$bin" -i "$prompt" ;;
        opencode) "$bin" --prompt "$prompt" ;;
      esac ;;
    semi)
      if copy_clip "$prompt"; then say "开场白已复制到剪贴板。"; else say "开场白如下（请复制）："; say "    $prompt"; fi
      say "正在打开 $(tool_name "$t")。打开后按 Command+V 粘贴开场白并回车；它说\"本阶段做完了\"你就关掉它，回到这个窗口。"
      line
      "$bin" ;;
    *)
      if copy_clip "$prompt"; then say "开场白已复制到剪贴板。"; else say "开场白如下（请复制）："; fi
      say "    $prompt"
      say "现在打开你的编码工具，让它打开工具包文件夹，粘贴上面这句并发送。它说\"本阶段做完了\"你就回到这个窗口。"
      ask "聊完后按回车继续："
      ;;
  esac
  line
}

# ======================================================================
# 主路线：AI 在对话里调用的子命令（不交互）
# ======================================================================
STAGE="$(sget 阶段)"; STATUS="$(sget 状态)"
case "$CMD" in
  状态) show_status; exit 0 ;;

  任务)
    if [ "$STAGE" = "完成" ]; then say "这个产品的流程已经走完。要再改，先：bash kit.sh 回到 ${PRODUCT} 阶段X"; exit 0; fi
    if [ "$STATUS" = "等验收" ]; then
      say "${STAGE} 的自检已经通过，现在等产品经理验收。"
      say "把 docs/evidence/${STAGE}/照着做清单.md 讲给他，请他验收；他说\"验收通过\"后运行：bash kit.sh 通过 ${PRODUCT}"
      say "他要改东西，就改完再跑一次：bash kit.sh 自检 ${PRODUCT}"
      exit 0
    fi
    write_task "$STAGE" chat; sset 状态 做中; hist "${STAGE} 开工（第 $(sget 开工次数) 次，对话模式）"
    cat "$TASK"; exit 0 ;;

  自检)
    if [ "$STAGE" = "完成" ]; then say "流程已完成，没有要自检的阶段。"; exit 0; fi
    run_verify "$STAGE"; rc=$?
    line
    if [ "$rc" -eq 0 ]; then say "管家记录：${STAGE} 自检通过，状态=等验收。下一步请产品经理验收；他说\"验收通过\"后运行：bash kit.sh 通过 ${PRODUCT}"
    else say "管家记录：${STAGE} 自检没过，状态=自检没过。先补上面报的项，再跑一次自检。不能进下一阶段。"; fi
    exit "$rc" ;;

  通过)
    WANT="${1:-}"
    if [ "$STAGE" = "完成" ]; then say "流程已完成。"; exit 0; fi
    if [ "$STATUS" != "等验收" ]; then
      say "拒绝：${STAGE} 的状态是\"${STATUS}\"，不是\"等验收\"。先运行 bash kit.sh 自检 ${PRODUCT} 并通过，再请产品经理验收。"; exit 1
    fi
    next_options "$STAGE"
    if [ -n "$NEXT_FIXED" ]; then
      [ -n "$WANT" ] && [ "$WANT" != "$NEXT_FIXED" ] && { say "拒绝：${STAGE} 之后只能是 ${NEXT_FIXED}。"; exit 1; }
      NEXT="$NEXT_FIXED"
    else
      if [ -z "$WANT" ]; then
        say "${STAGE} 之后有分岔，请先问产品经理，再带上下一阶段名重新运行：bash kit.sh 通过 ${PRODUCT} <下一阶段>"
        explain_options "$STAGE"; exit 1
      fi
      ok=0; for o in $NEXT_OPTS; do [ "$o" = "$WANT" ] && ok=1; done
      [ "$ok" -eq 1 ] || { say "拒绝：${STAGE} 之后只能是：${NEXT_OPTS}"; explain_options "$STAGE"; exit 1; }
      NEXT="$WANT"
    fi
    advance "$STAGE" "$NEXT"
    if [ "$NEXT" = "完成" ]; then say "已记录：${STAGE} 验收通过。全部完成！"; else say "已记录：${STAGE} 验收通过，翻到 ${NEXT}。下一步运行：bash kit.sh 任务 ${PRODUCT}"; fi
    exit 0 ;;

  回到)
    WANT="${1:-}"; valid_stage "$WANT" || { say "用法：bash kit.sh 回到 ${PRODUCT} 阶段2-1（阶段名要像 阶段0、阶段2-1、阶段3-0 这样）"; exit 1; }
    sset 阶段 "$WANT"; sset 状态 待开工; sset 开工次数 0; hist "手动校正到 ${WANT}"; say "已回到 ${WANT}。"; exit 0 ;;

  换工具) sset 工具 ""; say "好，下次双击开工会重新问用哪个工具。"; exit 0 ;;
  "") ;;
  *) say "不认识的子命令：${CMD}。可用：任务 / 自检 / 通过 / 状态 / 回到 / 换工具（见 kit.sh 开头）"; exit 1 ;;
esac

# ======================================================================
# 副路线：双击 开工.command（交互）
# ======================================================================
if [ "$STAGE" = "完成" ]; then show_status; say "这个产品的流程已经走完。"; exit 0; fi
show_status

if [ "$STATUS" = "等验收" ]; then
  say "上次自检已通过，现在轮到你验收：按 docs/evidence/${STAGE}/照着做清单.md 点一遍（无代码阶段就是看文档、确认该确认的事）。"
  if ask_yn "验收通过了吗？"; then
    next_options "$STAGE"
    if [ -n "$NEXT_FIXED" ]; then NEXT="$NEXT_FIXED"
    else
      say "接下来去哪？"; explain_options "$STAGE"
      while :; do ask "输入阶段名（如 $(echo $NEXT_OPTS | cut -d' ' -f1)）："; ok=0; for o in $NEXT_OPTS; do [ "$o" = "$ANS" ] && ok=1; done; [ "$ok" -eq 1 ] && { NEXT="$ANS"; break; }; say "只能是：${NEXT_OPTS}"; done
    fi
    advance "$STAGE" "$NEXT"
    if [ "$NEXT" = "完成" ]; then say "全部完成！"; exit 0; fi
    say "已翻到 ${NEXT}。"
    if ! ask_yn "现在就开始做 ${NEXT} 吗？"; then say "好，下次双击开工继续。"; exit 0; fi
    STAGE="$NEXT"; STATUS=待开工; show_status
  else
    sset 状态 做中; hist "${STAGE} 验收未通过，继续改"
    say "好，留在 ${STAGE}。把没通过的地方告诉 AI 去改。"
    if ! ask_yn "现在打开工具继续改吗？"; then exit 0; fi
  fi
fi

TOOL="$(sget 工具)"
if [ -z "$TOOL" ]; then choose_tool; sset 工具 "$TOOL"; fi
write_task "$STAGE" window; sset 状态 做中; hist "${STAGE} 开工（第 $(sget 开工次数) 次，工具 $(tool_name "$TOOL")）"
PROMPT="请读取 projects/${PRODUCT}/docs/本阶段任务.md，按其中要求做 ${STAGE}。"
launch_tool "$TOOL" "$PROMPT"

say "工具已关闭，现在跑自检：bash verify.sh ${PRODUCT} ${STAGE}"
line
run_verify "$STAGE"; RC=$?
line
if [ "$RC" -eq 0 ]; then
  say "自检通过。下一步轮到你验收：按 docs/evidence/${STAGE}/照着做清单.md 点一遍（无代码阶段就是看文档、确认该确认的事）。"
  say "验收完再双击开工，回答\"验收通过了吗\"。"
else
  say "自检没过（上面有哪几项）。不能进下一阶段。下次双击开工，AI 会看到没过的项并补齐。"
fi
