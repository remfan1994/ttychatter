#!/usr/bin/env bash

# ==========================================================
# ttychatter.bash (bash-only edition)
# ----------------------------------------------------------
# Dependency-light OpenRouter terminal chat client.
#
# PROJECT ROLE
#   This edition exists for users who want the project goal -- a friendly,
#   usable terminal AI chat client -- without depending on Python 3. It is not
#   a developer curl example and it is not a one-shot wrapper. It is an actual
#   end-user interactive chat program, scaled to the tools likely present on
#   old or constrained systems that can still run curl over HTTPS.
#
# REQUIRED TOOLS
#   bash, curl, sed, awk, grep, tr, printf, mktemp, od, ln (with -T)
#
# OPTIONAL TOOLS
#   jq      - required for web search and lossless streaming; recommended for JSON
#   base64  - needed for binary/media file attachment support
#   file    - improves MIME type detection
#
# RUNTIME COMMAND MODE
#   Bash-only cannot offer ncurses function-key screens, so it now provides
#   colon commands inside the chat loop. A submitted input beginning with : is
#   handled locally as a client command instead of being sent to OpenRouter.
#
# DESIGN LIMIT
#   JSON is the hard part. Without Python or jq, JSON parsing is best-effort.
#   The script still tries to provide sessions, model listing, model testing,
#   memory, logs, code-block extraction, and attachment support.
# ==========================================================


# ==========================================================
# MAINTAINER COMMENTARY: BASH-ONLY PROJECT CONTRACT
# ----------------------------------------------------------
# This file is not a toy curl example.  It is the dependency-light end-user
# client in the ttychatter family.  The mission is still the same:
# provide a friendly terminal chat client for AI productivity without forcing
# a browser or a large runtime stack.
#
# The difference from bash/python3/ttychatter.python is dependency
# posture.  This edition must avoid Python.  It can rely on curl and ordinary
# shell-era tools because any machine that can realistically talk to OpenRouter over
# HTTPS already needs a working curl/TLS stack, but it should not assume Python
# 3 is installed.  Optional helpers such as jq, base64, and file may improve
# behavior, but the program should remain usable without them where possible.
#
# This creates maintenance tension: OpenRouter speaks JSON, and shell is not good
# at JSON.  The comments in this file are therefore especially important.  When
# you see a sed/awk parser, understand that it is a compatibility compromise,
# not a claim that regex is the ideal JSON engine.  If jq is available, use it.
# If not, the fallback should be conservative, human-readable, and honest about
# limitations.
#
# The bash-only edition should stay as featureful as it can be while remaining
# light on dependencies.  Do not intentionally reduce it to a one-shot prompt
# sender.  Interactive chat, sessions, config, logs, model selection, memory,
# and attachments all belong here if they can be implemented responsibly.
# ==========================================================

set -o pipefail
umask 077

PROGRAM="ttychatter.bash"
VERSION="3.19-web-limits"
XDG_CONFIG_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}"
XDG_DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}"
XDG_CACHE_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}"
CONFIG_DIR="$XDG_CONFIG_ROOT/ttychatter/openrouter"
CONFIG_FILE="$CONFIG_DIR/config"
LEGACY_CONFIG_FILE="$XDG_CONFIG_ROOT/ttychatter/config"
CONFIG_READ_FILE="$CONFIG_FILE"
[ ! -f "$CONFIG_READ_FILE" ] && [ -f "$LEGACY_CONFIG_FILE" ] && CONFIG_READ_FILE="$LEGACY_CONFIG_FILE"
MODEL_CACHE_FILE="$XDG_CACHE_ROOT/ttychatter/openrouter/models.json"
MODEL_CACHE_FILE_CONFIGURED=0
LEGACY_MODEL_CACHE_FILE="$CONFIG_DIR/models-cache.json"
API_KEY_GPG_FILE="$CONFIG_DIR/api-key.gpg"
LEGACY_API_KEY_GPG_FILE="$XDG_CONFIG_ROOT/ttychatter/api-key.gpg"
SESSION_DIR="$XDG_DATA_ROOT/ttychatter/openrouter/sessions"
DEFAULT_ATTACHMENT_DIR="$XDG_DATA_ROOT/ttychatter/openrouter/attachments"
ATTACHMENT_DIR=""
ATTACHMENT_DIR_CONFIGURED=0
MODEL=""
MODEL_TYPE_FILTER="all"
MODEL_SORT_ORDER="name"
MODEL_MIN_INPUT_TOKENS=0
MODEL_MIN_OUTPUT_TOKENS=0
MODEL_FILTER_REQUIRE_TOKENS=0
MODEL_FILTER_HIDE_PREVIEW=0
MODEL_FAVORITES_FILE="$CONFIG_DIR/model-favorites"
LEGACY_MODEL_FAVORITES_FILE="$CONFIG_DIR/model-favorites.txt"
THEME="default"
SEND_INPUT="ctrl_d"
CODE_ATTACHMENT_MIN_LINES=5
FALLBACK_MODEL="openrouter/auto"
CONTEXT_TURNS=8
OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-${TTYCHATTER_API_KEY:-}}"
MODEL_TEST_PROMPT="Please reply with a short sentence confirming this model is available for text generation."
DEMO_MODE=0
STARTUP_NOTICE=1
PROJECT_LEAD_NAME="remfan1994"
PROJECT_LEAD_NOTICE="Everyone is encouraged to get the cruelty-free vegetarian alternatives and remember the bloodguilt curse from the Bible... http://bloodguiltcurse.net"
TC_PRECHAT_LINE="$PROJECT_LEAD_NOTICE"
STREAM_INTERRUPT_MARKER="[assistant stream interrupted by user]"
USER_NAME="$(whoami 2>/dev/null || printf user)"
CONTEXT_BUFFER=()
ATTACH_FILES=()
ACTION="chat"
SESSION_NAME=""
RESUME=0
SAVE=0
TEST_MODEL=""
SET_KEY=""
SET_VALUE=""
UNSET_KEY=""
RUNTIME_SEND_OVERRIDE=""
LOOPBACK=0
LOOPBACK_FILE=""
SESSION_AUTO_TITLE=0
SESSION_TITLE_MODEL="openrouter/auto"
SESSION_TITLE_MAX_WORDS=8
STREAM=0
STATUS_UPDATES=0
VOICE_INPUT_CMD=""
VOICE_OUTPUT_CMD=""
VOICE_OUTPUT=0
AI_FILE_DOWNLOAD_MODE="ask"
WEB_SEARCH=0
WEB_SEARCH_ENGINE="auto"
WEB_SEARCH_MAX_RESULTS=5
WEB_SEARCH_MAX_USES=3
PROMPT_SESSION_NAME=1
SESSION_ID=""
SESSION_FIXED_PATH=0
PROMPT_TEXT=""
INPUT_FILE=""
OUTPUT_PATH=""
INTERACTIVE=0
FORCE_NEW=0
TURN_SESSION=""
TURN_NUMBER=""
EXPORT_SESSION=""
EXPORT_FORMAT="markdown"
VOICE_INPUT_PATH=""
SPEAK_OVERRIDE=""
SEARCH_QUERY=""
SEARCH_ALL=0

# ==========================================================
# BASIC HELPERS
# ==========================================================

# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function timestamp
# Produces timestamps for prompts and logs. The log reader expects this
# stable shape.
# ----------------------------------------------------------
timestamp() { date +"%H:%M:%S"; }
# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function trim
# trim is part of the dependency-light client behavior. Changes should
# preserve old-system usability and update docs/comments if behavior
# changes.
# ----------------------------------------------------------
trim() { printf '%s' "$1" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'; }
# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function truthy
# truthy is part of the dependency-light client behavior. Changes should
# preserve old-system usability and update docs/comments if behavior
# changes.
# ----------------------------------------------------------
truthy() { case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in 1|yes|y|true|on) return 0;; *) return 1;; esac; }
# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function normalize_model_id
# normalize_model_id is part of the dependency-light client behavior.
# Changes should preserve old-system usability and update docs/comments if
# behavior changes.
# ----------------------------------------------------------
normalize_model_id() { case "$1" in models/*) printf '%s\n' "${1#models/}";; *) printf '%s\n' "$1";; esac; }
sanitize_session_name() {
  local cleaned
  cleaned="$(printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_' | sed 's/^[_ .-]*//; s/[_ .-]*$//')"
  [ -n "$cleaned" ] || cleaned="session"
  printf '%s\n' "$cleaned"
}
effective_model() { if [ -n "$MODEL" ]; then normalize_model_id "$MODEL"; else printf '%s
' "$FALLBACK_MODEL"; fi; }
model_label() { if [ -n "$MODEL" ]; then normalize_model_id "$MODEL"; else printf '%s (fallback; no MODEL configured)
' "$FALLBACK_MODEL"; fi; }
# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function json_escape_stream
# json_escape_stream is part of the dependency-light client behavior.
# Changes should preserve old-system usability and update docs/comments if
# behavior changes.
# ----------------------------------------------------------
json_escape_stream() { LC_ALL=C awk 'BEGIN { for(i=1;i<32;i++) esc[sprintf("%c",i)]=sprintf("\\u%04x",i) } { if(NR>1) printf "\\n"; for(i=1;i<=length($0);i++) { c=substr($0,i,1); if(c=="\\") printf "\\\\"; else if(c=="\"") printf "\\\""; else if(c in esc) printf "%s",esc[c]; else printf "%s",c; } }'; }
# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function json_unescape_basic
# json_unescape_basic is part of the dependency-light client behavior.
# Changes should preserve old-system usability and update docs/comments if
# behavior changes.
# ----------------------------------------------------------
json_unescape_basic() { sed 's/\\n/\
/g; s/\\t/	/g; s/\\"/"/g; s/\\\\/\\/g'; }

# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function decrypt_gpg_key_file
# Optional GPG key loading keeps encrypted API-key storage available even in
# the dependency-light Bash edition. This function intentionally invokes the
# user's normal gpg command instead of trying to implement cryptography in
# shell. If gpg is missing or decryption fails, the function simply returns
# failure and the normal API-key error path explains what is missing.
# ----------------------------------------------------------
decrypt_gpg_key_file() {
  local path="$1"
  [ -f "$path" ] || return 1
  command -v gpg >/dev/null 2>&1 || return 1
  gpg --quiet --decrypt "$path" 2>/dev/null | tr -d '\r' | sed '/^[[:space:]]*$/d' | head -n 1
}


# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function encrypt_gpg_key_file
# Bash-only encrypted API-key storage. This function uses the external gpg
# command as the cryptographic engine because implementing encryption in Bash
# would be both insecure and contrary to the purpose of using the user's normal
# Unix tools. The temporary plaintext file is chmod 0600 where possible and is
# always removed after gpg returns. If gpg is unavailable or fails, callers must
# not silently save plaintext; they should leave the key runtime-only or ask the
# user explicitly.
# ----------------------------------------------------------
encrypt_gpg_key_file() {
  local path="$1"
  local secret="$2"
  local tmp
  local status

  command -v gpg >/dev/null 2>&1 || { printf '%s\n' "gpg not found; encrypted API-key storage unavailable" >&2; return 1; }
  mkdir -p "$(dirname "$path")"
  tmp="$(mktemp "${TMPDIR:-/tmp}/ttychatter-api-key.XXXXXX")" || return 1
  chmod 600 "$tmp" 2>/dev/null || true
  printf '%s\n' "$secret" > "$tmp"

  gpg --symmetric --armor --yes --output "$path" "$tmp"
  status=$?
  rm -f "$tmp"
  if [ "$status" -eq 0 ]; then
    chmod 600 "$path" 2>/dev/null || true
    return 0
  fi
  return "$status"
}

# ==========================================================
# CONFIG FILE
# ==========================================================

# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function load_config
# Reads the shared config format using only shell tools. Keep it simple so
# old systems and future C code can understand the same settings.
# ----------------------------------------------------------
load_config() {
  [ -f "$CONFIG_READ_FILE" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="$(trim "$line")"
    case "$line" in ''|\#*) continue ;; esac
    case "$line" in *=*) key="$(trim "${line%%=*}")"; value="$(trim "${line#*=}")" ;; *) continue ;; esac
    case "$key" in
      MODEL) MODEL="$(normalize_model_id "$value")" ;;
      CONTEXT_TURNS|HISTORY_LIMIT) CONTEXT_TURNS="$value" ;;
      OPENROUTER_API_KEY|API_KEY) OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-$value}" ;;
      API_KEY_GPG_FILE|OPENROUTER_API_KEY_GPG_FILE) API_KEY_GPG_FILE="$value" ;;
      SESSION_DIR) SESSION_DIR="${value/#\~/$HOME}" ;;
      ATTACHMENT_DIR) ATTACHMENT_DIR="${value/#\~/$HOME}"; ATTACHMENT_DIR_CONFIGURED=1 ;;
      AI_FILE_DOWNLOAD_MODE|AI_DOWNLOAD) AI_FILE_DOWNLOAD_MODE="$value" ;;
      WEB_SEARCH) WEB_SEARCH="$value" ;;
    WEB_SEARCH_ENGINE) WEB_SEARCH_ENGINE="$value" ;;
    WEB_SEARCH_MAX_RESULTS) WEB_SEARCH_MAX_RESULTS="$value" ;;
    WEB_SEARCH_MAX_USES) WEB_SEARCH_MAX_USES="$value" ;;
    PROMPT_SESSION_NAME) PROMPT_SESSION_NAME="$value" ;;
    MODEL_TEST_PROMPT) [ -n "$value" ] && MODEL_TEST_PROMPT="$value" ;;
      MODEL_TYPE_FILTER) MODEL_TYPE_FILTER="$value" ;;
      MODEL_SORT_ORDER) MODEL_SORT_ORDER="$value" ;;
      MODEL_MIN_INPUT_TOKENS) MODEL_MIN_INPUT_TOKENS="$value" ;;
      MODEL_MIN_OUTPUT_TOKENS) MODEL_MIN_OUTPUT_TOKENS="$value" ;;
      MODEL_FILTER_REQUIRE_TOKENS) MODEL_FILTER_REQUIRE_TOKENS="$value" ;;
      MODEL_FILTER_HIDE_PREVIEW) MODEL_FILTER_HIDE_PREVIEW="$value" ;;
      MODEL_CACHE_FILE) MODEL_CACHE_FILE="${value/#\~/$HOME}"; MODEL_CACHE_FILE_CONFIGURED=1 ;;
      MODEL_FAVORITES_FILE) MODEL_FAVORITES_FILE="${value/#\~/$HOME}" ;;
      THEME) THEME="$value" ;;
      SEND_INPUT) SEND_INPUT="$value" ;;
      CODE_ATTACHMENT_MIN_LINES) CODE_ATTACHMENT_MIN_LINES="$value" ;;
      DEMO_MODE) DEMO_MODE="$value" ;;
      STARTUP_NOTICE) STARTUP_NOTICE="$value" ;;
      SESSION_AUTO_TITLE) SESSION_AUTO_TITLE="$value" ;;
      SESSION_TITLE_MODEL) SESSION_TITLE_MODEL="$(normalize_model_id "$value")" ;;
      SESSION_TITLE_MAX_WORDS) SESSION_TITLE_MAX_WORDS="$value" ;;
      STREAM) STREAM="$value" ;;
      STATUS_UPDATES|PROGRESS_UPDATES) STATUS_UPDATES="$value" ;;
      VOICE_INPUT_CMD|TRANSCRIBE_CMD) VOICE_INPUT_CMD="$value" ;;
      VOICE_OUTPUT_CMD|TTS_CMD) VOICE_OUTPUT_CMD="$value" ;;
      VOICE_OUTPUT|TTS) VOICE_OUTPUT="$value" ;;
    esac
  done < "$CONFIG_READ_FILE"
}

# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function save_config_value
# Updates one KEY=VALUE setting while preserving the rest of the config
# file. This is the shell-only equivalent of the richer config writers
# elsewhere.
# ----------------------------------------------------------
save_config_value() {
  local key="$1" value="$2" tmp
  mkdir -p "$CONFIG_DIR"
  tmp="$(mktemp "${CONFIG_FILE}.tmp.XXXXXX")"
  if [ -f "$CONFIG_FILE" ]; then
    awk -v k="$key" -v v="$value" 'BEGIN{done=0} $0 ~ "^" k "=" {print k "=" v; done=1; next} {print} END{if(!done) print k "=" v}' "$CONFIG_FILE" > "$tmp"
  else
    printf '%s=%s\n' "$key" "$value" > "$tmp"
  fi
  mv "$tmp" "$CONFIG_FILE"
  chmod 600 "$CONFIG_FILE" 2>/dev/null || true
}


# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function remove_config_key
# Deletes one setting from the config file.  This is deliberately simple and
# line-oriented because the bash-only edition must stay usable without Python
# or structured config parsers.  It is used by --unset and by runtime colon
# commands such as :unset and :forget-api-key.
# ----------------------------------------------------------
remove_config_key() {
  local key="$1" tmp
  [ -f "$CONFIG_FILE" ] || return 0
  tmp="$(mktemp "${CONFIG_FILE}.tmp.XXXXXX")"
  awk -v k="$key" '$0 !~ "^" k "=" { print }' "$CONFIG_FILE" > "$tmp"
  mv "$tmp" "$CONFIG_FILE"
  chmod 600 "$CONFIG_FILE" 2>/dev/null || true
}


# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function set_api_key_command
# Provides the dependency-light client with the same security choice as the
# ncurses GUI: runtime-only by default, optional GPG-encrypted storage when gpg
# exists, and plaintext storage only after a clear warning.  This is deliberately
# interactive because secrets should not be written because of an accidental
# command-line flag.  The bash-only edition cannot show a checkbox, but the
# sequence of prompts mirrors the GUI decision points.
# ----------------------------------------------------------
set_api_key_command() {
  local key encrypt_answer answer gpg_path

  printf 'Paste OpenRouter API key: '
  IFS= read -r -s key
  printf '\n'
  [ -n "$key" ] || { printf '%s\n' "No key entered."; return 1; }
  OPENROUTER_API_KEY="$key"

  if command -v gpg >/dev/null 2>&1; then
    printf 'Encrypt with gpg? [y/N] '
    IFS= read -r encrypt_answer
    case "$encrypt_answer" in
      y|Y|yes|YES)
        gpg_path="$API_KEY_GPG_FILE"
        if encrypt_gpg_key_file "$gpg_path" "$key"; then
          save_config_value "API_KEY_GPG_FILE" "$gpg_path"
          remove_config_key "OPENROUTER_API_KEY"
          remove_config_key "TTYCHATTER_API_KEY"
          printf 'saved encrypted API key to %s\n' "$gpg_path"
          return 0
        fi
        printf '%s\n' "Encrypted save failed; key is active for this run only."
        return 1
        ;;
    esac
  else
    printf '%s\n' "gpg not found; encrypted API-key storage unavailable."
  fi

  printf '%s\n' "WARNING: key stored in plaintext if saved to config."
  printf 'Save plaintext OPENROUTER_API_KEY to %s? [y/N] ' "$CONFIG_FILE"
  IFS= read -r answer
  case "$answer" in
    y|Y|yes|YES)
      save_config_value "OPENROUTER_API_KEY" "$key"
      printf 'saved OPENROUTER_API_KEY to %s\n' "$CONFIG_FILE"
      ;;
    *)
      printf '%s\n' "API key active for this run only."
      ;;
  esac
}

forget_api_key_command() {
  remove_config_key OPENROUTER_API_KEY
  remove_config_key TTYCHATTER_API_KEY
  printf '%s\n' "removed plaintext API-key config entries if present"
}

load_config
if [ -z "$OPENROUTER_API_KEY" ]; then
  OPENROUTER_API_KEY="$(decrypt_gpg_key_file "$API_KEY_GPG_FILE" || true)"
fi
if [ -z "$OPENROUTER_API_KEY" ] && [ -f "$LEGACY_API_KEY_GPG_FILE" ]; then
  OPENROUTER_API_KEY="$(decrypt_gpg_key_file "$LEGACY_API_KEY_GPG_FILE" || true)"
fi
case "$CONTEXT_TURNS" in ''|*[!0-9]*) CONTEXT_TURNS=8 ;; esac
[ "$CONTEXT_TURNS" -lt 1 ] && CONTEXT_TURNS=8
case "$MODEL_MIN_INPUT_TOKENS" in ''|*[!0-9]*) MODEL_MIN_INPUT_TOKENS=0 ;; esac
case "$MODEL_MIN_OUTPUT_TOKENS" in ''|*[!0-9]*) MODEL_MIN_OUTPUT_TOKENS=0 ;; esac
case "$CODE_ATTACHMENT_MIN_LINES" in ''|*[!0-9]*) CODE_ATTACHMENT_MIN_LINES=5 ;; esac
[ "$CODE_ATTACHMENT_MIN_LINES" -lt 1 ] && CODE_ATTACHMENT_MIN_LINES=5
[ "$ATTACHMENT_DIR_CONFIGURED" -eq 0 ] && ATTACHMENT_DIR="$DEFAULT_ATTACHMENT_DIR"
mkdir -p "$CONFIG_DIR" "$SESSION_DIR" "$ATTACHMENT_DIR" "$(dirname "$MODEL_CACHE_FILE")"
if [ ! -e "$MODEL_FAVORITES_FILE" ] && [ -f "$LEGACY_MODEL_FAVORITES_FILE" ]; then
  cp "$LEGACY_MODEL_FAVORITES_FILE" "$MODEL_FAVORITES_FILE" 2>/dev/null || true
fi

# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function require_api_key
# require_api_key is part of the dependency-light client behavior. Changes
# should preserve old-system usability and update docs/comments if behavior
# changes.
# ----------------------------------------------------------
require_api_key() {
  if truthy "$DEMO_MODE"; then
    return 0
  fi
  if [ -z "$OPENROUTER_API_KEY" ]; then
    printf '%s\n' "ERROR: OPENROUTER_API_KEY not set." >&2
    printf '%s\n' "Set it with: export OPENROUTER_API_KEY=your_key" >&2
    printf '%s\n' "or save it in: $CONFIG_FILE"
    return 1
  fi
  return 0
}

# ==========================================================
# HELP / CREDITS
# ==========================================================

# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function help_menu
# Primary built-in documentation for users who may not have installed man
# pages.
# ----------------------------------------------------------
help_menu() {
  cat <<EOF
$PROGRAM (bash-only edition) - dependency-light OpenRouter terminal chat client

USAGE
    $PROGRAM
    $PROGRAM input.txt
    $PROGRAM input.txt output.log
    $PROGRAM -i [session]
    $PROGRAM --prompt TEXT
    $PROGRAM --input FILE [--output FILE]
    $PROGRAM --session SESSION
    $PROGRAM --resume <session>
    $PROGRAM --list
    $PROGRAM --rename-session SESSION TITLE
    $PROGRAM --search QUERY [--all-sessions]
    $PROGRAM --models [--model-type TYPE]
    $PROGRAM --favorites
    $PROGRAM --favorite-model MODEL
    $PROGRAM --unfavorite-model MODEL
    $PROGRAM --config
    $PROGRAM --set KEY VALUE
    $PROGRAM --unset KEY
    $PROGRAM --theme THEME
    $PROGRAM --send-input MODE
    $PROGRAM --code-attachment-min-lines N
    $PROGRAM --update-models
    $PROGRAM --select-model [--save]
    $PROGRAM --test-model <model> [--save]
    $PROGRAM --set-api-key
    $PROGRAM --forget-api-key
    $PROGRAM --attach FILE [session]
    $PROGRAM --stream | --no-stream
    $PROGRAM --web | --no-web
    $PROGRAM --web-engine ENGINE --web-max-results N --web-max-uses N
    $PROGRAM --ai-download ask|auto|metadata|off
    ESC during streaming stops the active streamed response and saves the partial reply.
    --ai-download controls provider-returned files: ask, auto, metadata, or off.
    $PROGRAM --status | --no-status
    $PROGRAM --voice-input FILE | --transcribe FILE
    $PROGRAM --speak | --no-speak
    $PROGRAM --turns SESSION
    $PROGRAM --branch SESSION TURN
    $PROGRAM --export SESSION [--format markdown|text] [--output FILE]
    $PROGRAM --loopback [session]
    $PROGRAM --loopback-file PATH [session]
    $PROGRAM --credits
    $PROGRAM --doctor
    $PROGRAM --version
    $PROGRAM --help

GPG API KEY STORAGE
    --set-api-key offers runtime-only, GPG-encrypted, or plaintext config storage.
    If gpg is not installed, the command says "gpg not found" and skips encrypted storage.
    Plaintext saves warn before writing OPENROUTER_API_KEY to config.

LOOPBACK OUTPUT
    --loopback routes interface text to stderr and writes AI responses to stdout.
    This is for piping ttychatter output into another program.

    --loopback-file PATH appends each AI response to PATH.

INPUT
    Type or paste a message, then send according to SEND_INPUT.
    SEND_INPUT=ctrl_d keeps multiline Ctrl+D behavior.
    SEND_INPUT=enter reads one line per message.

SESSION TITLES
    Named logs use TITLE--ID.log; --list shows the friendly name and actual filename.
    --rename-session updates the title and the actual log filename without overwriting another session.
    PROMPT_SESSION_NAME=1 asks for names in interactive unnamed sessions.
    Optional AI title generation is off by default: SESSION_AUTO_TITLE=0.

OPENROUTER HELP
    Model docs:      https://openrouter.ai/docs/models
    Models API:      https://openrouter.ai/docs/api-reference/models/get-models
    Chat completions: https://openrouter.ai/docs/api-reference/chat/send-chat-completion-request

RUNTIME COMMAND MODE
    During a running chat, submit a line beginning with ':' to run a local
    command instead of sending a message to the AI backend.

    Examples:
        :help
        :rename notes
        :models
        :favorites
        :favorite openrouter/auto
        :unfavorite openrouter/auto
        :select-model
        :model openrouter/auto
        :memory
        :attach /path/to/file.txt
        :editor
        :credits

MODEL FILTER ALIASES
    --routers                       Show router/free routes in model list
    --show-preview | --hide-preview Control model preview text
    --require-tokens | --allow-missing-tokens
                                    Control token metadata filtering
    --min-input-tokens N             Filter by input-token capacity when available
    --min-output-tokens N            Filter by output-token capacity when available
    --unbookmark-model MODEL         Alias for --unfavorite-model

COMPATIBILITY / TESTING
    --demo                           Use local demo responses and demo model rows
    --interactive                    Alias for -i
    --new                            Force a fresh timestamped session
    --progress | --no-progress       Aliases for --status/--no-status
    ESC during streaming              Stop the active streamed response and save the partial reply
    :ai-download MODE                 AI file receipt: ask, auto, metadata, off
    --edit-turn SESSION TURN         Not implemented in bash-only; use C or python helper

NOTES
    This edition uses Bash and basic Unix tools only. JSON parsing is
    best-effort without Python or jq, but the program remains an end-user chat
    client with sessions, memory, logs, model selection, and attachments.
EOF
}

# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function credits
# credits is part of the dependency-light client behavior. Changes should
# preserve old-system usability and update docs/comments if behavior
# changes.
# ----------------------------------------------------------
credits() {
  cat <<'EOF'
ttychatter
GitHub: https://github.com/remfan1994/ttychatter

Project focus:
  User-friendly and robust AI chat in the text terminal. Browsers and browser
  interfaces are not required for sending text messages and attachments back
  and forth to an AI.

Project Lead notice:
  Everyone is encouraged to get the cruelty-free vegetarian alternatives and remember the bloodguilt curse from the Bible... http://bloodguiltcurse.net
EOF
}

demo_models_json() {
  cat <<'JSON'
{"data":[{"id":"demo/auto","name":"Demo Auto Router","description":"Local demo model. No network request is made.","context_length":8192,"architecture":{"modality":"text"}},{"id":"demo/free","name":"Demo Free Route","description":"Local demo route for UI testing.","context_length":4096,"architecture":{"modality":"text"}},{"id":"demo/code-helper","name":"Demo Code Helper","description":"Local demo row for code-block and attachment testing.","context_length":32768,"architecture":{"modality":"text"}}]}
JSON
}

demo_ai_response() {
  local prompt="$1"
  printf '%s\n' "Demo mode is active; no OpenRouter request was made."
  printf 'Prompt excerpt: %s\n' "$(printf '%s' "$prompt" | tr '\n' ' ' | cut -c 1-140)"
  printf '\nShort inline code block:\n```text\nok\n```\n'
  printf '\nLong code block for attachment-threshold testing:\n```sh\necho ttychatter demo mode\necho no provider request was made\necho long code blocks should become attachments\necho done\n```\n'
}

# ==========================================================
# MODEL LIST / CACHE
# ==========================================================

# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function fetch_models_json
# fetch_models_json is part of the dependency-light client behavior. Changes
# should preserve old-system usability and update docs/comments if behavior
# changes.
# ----------------------------------------------------------
fetch_models_json() { if truthy "$DEMO_MODE"; then demo_models_json; return 0; fi; require_api_key || return 1; curl -sS --max-time 60 -H "Authorization: Bearer $OPENROUTER_API_KEY" "https://openrouter.ai/api/v1/models"; }
# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function update_models_cache
# update_models_cache is part of the dependency-light client behavior.
# Changes should preserve old-system usability and update docs/comments if
# behavior changes.
# ----------------------------------------------------------
update_models_cache() {
  local tmp
  mkdir -p "$(dirname "$MODEL_CACHE_FILE")" || return 1
  tmp="$(mktemp "${MODEL_CACHE_FILE}.tmp.XXXXXX")" || return 1
  if ! fetch_models_json > "$tmp"; then rm -f "$tmp"; return 1; fi
  if command -v jq >/dev/null 2>&1; then
    jq -e 'type=="object" and (.data|type=="array") and (.error|not)' "$tmp" >/dev/null 2>&1
  else
    grep -Eq '"data"[[:space:]]*:[[:space:]]*\[' "$tmp"
  fi
  if [ "$?" -ne 0 ]; then
    rm -f "$tmp"; printf '%s\n' 'Invalid model catalog; existing cache preserved.' >&2; return 1
  fi
  mv "$tmp" "$MODEL_CACHE_FILE" || { rm -f "$tmp"; return 1; }
  printf 'updated model cache: %s\n' "$MODEL_CACHE_FILE" >&2
}
# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function models_json_source
# models_json_source is part of the dependency-light client behavior.
# Changes should preserve old-system usability and update docs/comments if
# behavior changes.
# ----------------------------------------------------------
models_json_source() { if truthy "$DEMO_MODE"; then demo_models_json; return 0; fi; if [ -f "$MODEL_CACHE_FILE" ]; then cat "$MODEL_CACHE_FILE"; elif [ "$MODEL_CACHE_FILE_CONFIGURED" -eq 0 ] && [ -f "$LEGACY_MODEL_CACHE_FILE" ]; then cat "$LEGACY_MODEL_CACHE_FILE"; else fetch_models_json; fi; }
# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function parse_models_basic
# parse_models_basic is part of the dependency-light client behavior.
# Changes should preserve old-system usability and update docs/comments if
# behavior changes.
# ----------------------------------------------------------
parse_models_basic() {
  if command -v jq >/dev/null 2>&1; then
    jq -r '
      (.data // .models // [])[]
      | . as $m
      | [
          ($m.id // $m.name // ""),
          ($m.name // $m.id // ""),
          (($m.context_length // $m.contextLength // "")|tostring),
          (($m.max_completion_tokens // $m.maxCompletionTokens // "")|tostring),
          (if (($m.id // $m.name // "") == "openrouter/auto" or ($m.id // $m.name // "") == "openrouter/free" or ($m.id // $m.name // "") == "demo/auto" or ($m.id // $m.name // "") == "demo/free" or (($m.id // $m.name // "") | test(":free$"))) then "router" else "" end)
        ]
      | select(.[0] != "")
      | @tsv
    '
    return
  fi

  # Fallback parser for systems without jq. OpenRouter's model catalog is JSON,
  # and shell is not a JSON language. This fallback intentionally extracts only
  # model IDs from common compact/pretty-printed shapes. Users who need richer
  # token metadata on a dependency-light system should install jq; the program
  # remains useful without it because model ID selection and testing still work.
  tr ',' '\n' | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1\t\1\t\t\t/p'
}
# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function list_models
# Shows model candidates using jq when possible and sed/grep fallback
# otherwise. This is intentionally best-effort in bash-only.
# ----------------------------------------------------------

# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function model_type_filter_output
# OpenRouter has both fixed model IDs and router-like entries such as
# openrouter/auto and openrouter/free.  Bash has no dropdown UI, so this helper
# gives the command line the same conceptual feature as the ncurses router
# selector: users can ask for routers, fixed models, free routes, or auto.
# The parser uses visible metadata columns produced by parse_models_table rather
# than making a second network request.
# ----------------------------------------------------------
model_type_filter_output() {
  local type="${MODEL_TYPE_FILTER:-all}"
  awk -F '\t' -v t="$type" '
    BEGIN { IGNORECASE=1 }
    t == "all" { print; next }
    { id=$1; badges=$5 }
    t == "routers" && (id ~ /^openrouter\// || badges ~ /router|free/) { print; next }
    t == "fixed" && !(id ~ /^openrouter\// || badges ~ /router|free/) { print; next }
    t == "free" && (id == "openrouter/free" || id ~ /:free$/ || badges ~ /free/) { print; next }
    t == "auto" && (id == "openrouter/auto") { print; next }
  '
}

# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function decorate_filter_sort_models
# The ncurses model browser can show badges, bookmarks, and sort orders inside
# a table.  The bash-only edition cannot provide a clickable table, but it can
# still honor the same underlying choices.  This function therefore performs
# three jobs after parse_models_basic emits tab-separated rows:
#   1. add a "favorite" badge when the model ID appears in the favorites file;
#   2. apply basic token and type filters without requiring Python;
#   3. sort the rows using ordinary Unix sort where possible.
# This gives old machines a useful approximation of the ncurses model browser
# while preserving the low-dependency contract.
# ----------------------------------------------------------
decorate_filter_sort_models() {
  awk -F '\t' -v OFS='\t' \
      -v favfile="$MODEL_FAVORITES_FILE" \
      -v type="${MODEL_TYPE_FILTER:-all}" \
      -v minin="${MODEL_MIN_INPUT_TOKENS:-0}" \
      -v minout="${MODEL_MIN_OUTPUT_TOKENS:-0}" \
      -v require_tokens="${MODEL_FILTER_REQUIRE_TOKENS:-0}" '
    BEGIN {
      IGNORECASE=1
      while ((getline line < favfile) > 0) {
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)
        if (line != "") fav[line]=1
      }
    }
    {
      id=$1; inTok=$3+0; outTok=$4+0; badges=$5
      if (fav[id]) badges=(badges=="" ? "favorite" : badges ",favorite")
      isRouter=(id ~ /^openrouter\// || badges ~ /router|free/)
      isFree=(id == "openrouter/free" || id ~ /:free$/ || badges ~ /free/)
      if (type == "routers" && !isRouter) next
      if (type == "fixed" && isRouter) next
      if (type == "free" && !isFree) next
      if (type == "auto" && id != "openrouter/auto") next
      if (require_tokens ~ /^(1|yes|true|on)$/ && inTok <= 0) next
      if (inTok < minin) next
      if (outTok < minout) next
      $5=badges
      print
    }' | sort_models_basic
}

sort_models_basic() {
  case "$MODEL_SORT_ORDER" in
    favorites)
      awk -F '\t' 'BEGIN{OFS="\t"} {print (($5 ~ /favorite/) ? 0 : 1), $0}' | sort -t "$(printf '\t')" -k1,1n -k2,2 | cut -f2-
      ;;
    input|input-desc|input-tokens|tokens|context)
      sort -t "$(printf '\t')" -k3,3nr -k1,1
      ;;
    output|output-desc)
      sort -t "$(printf '\t')" -k4,4nr -k1,1
      ;;
    display)
      sort -t "$(printf '\t')" -k2,2 -k1,1
      ;;
    *)
      sort -t "$(printf '\t')" -k1,1
      ;;
  esac
}

list_models() { local json; json="$(models_json_source)" || return 1; printf '%s' "$json" | parse_models_basic | decorate_filter_sort_models; }

# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function select_model
# Interactive line-oriented model choice. It should remain usable even on
# old terminals.
# ----------------------------------------------------------
select_model() {
  local lines choice selected model display input output answer status
  mapfile -t lines < <(list_models)
  [ "${#lines[@]}" -gt 0 ] || { printf '%s\n' "No OpenRouter models found."; return 1; }
  local i=1 line
  for line in "${lines[@]}"; do IFS=$'\t' read -r model display input output <<< "$line"; printf '%3d) %s - %s (input:%s output:%s)\n' "$i" "$model" "$display" "${input:-?}" "${output:-?}"; i=$((i+1)); done
  printf 'Select model number: '; IFS= read -r choice
  case "$choice" in ''|*[!0-9]*) printf '%s\n' "Invalid selection."; return 1 ;; esac
  [ "$choice" -ge 1 ] && [ "$choice" -le "${#lines[@]}" ] || { printf '%s\n' "Selection out of range."; return 1; }
  selected="${lines[$((choice-1))]}"; IFS=$'\t' read -r model display input output <<< "$selected"
  printf 'Selected: %s\n' "$model"; printf 'Test selected model? [Y/n] '; IFS= read -r answer
  case "$answer" in n|N|no|NO) status=0 ;; *) test_model "$model"; status=$? ;; esac
  if [ "$SAVE" -eq 1 ] && [ "$status" -eq 0 ]; then save_config_value MODEL "$model"; printf 'saved MODEL=%s\n' "$model"; fi
}

# ==========================================================
# OPENROUTER REQUESTS
# ==========================================================

# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function build_prompt_with_attachments
# The bash-only edition deliberately keeps attachment handling simple. Text
# attachments are merged into the prompt because every chat model can read text.
# Binary/media files are represented by a visible notice instead of pretending
# this low-dependency shell path can safely build every possible multipart or
# multimodal request. The Python/ncurses edition remains the stronger media path.
# ----------------------------------------------------------
build_prompt_with_attachments() {
  local prompt="$1" file name mime
  printf '%s' "$prompt"
  for file in "${ATTACH_FILES[@]}"; do
    [ -f "$file" ] || { printf '\n\n[Attachment unavailable: %s]' "$file"; continue; }
    name="$(basename "$file")"
    if command -v file >/dev/null 2>&1; then mime="$(file -b --mime-type "$file" 2>/dev/null)"; else mime=""; fi
    case "$mime:$file" in
      text/*:*|*:*.txt|*:*.md|*:*.log|*:*.sh|*:*.c|*:*.h|*:*.py|*:*.json|*:*.yaml|*:*.yml)
        printf '\n\n[Attached text file: %s]\n' "$name"
        cat "$file"
        ;;
      *)
        printf '\n\n[Attachment not sent inline by ttychatter.bash: %s (%s)]' "$name" "${mime:-unknown MIME}"
        ;;
    esac
  done
}

# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function build_openrouter_payload
# Builds the OpenRouter chat-completions JSON request. JSON construction in
# bash-only is necessarily more fragile than in the Python-helper edition, so
# this function keeps all manual escaping in one place. If jq is installed, it
# is used for correct JSON generation. Otherwise a conservative escape routine
# is used.
# ----------------------------------------------------------
build_openrouter_payload() {
  local model="$1" prompt="$2" stream_flag="${3:-0}" web=0 stream=0 count
  normalize_web_options
  truthy "$WEB_SEARCH" && web=1
  truthy "$stream_flag" && stream=1
  count="$WEB_SEARCH_MAX_RESULTS"
  [ "$WEB_SEARCH_ENGINE" = perplexity ] && [ "$count" -gt 20 ] && count=20
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$prompt" | jq -Rs --arg model "$model" --arg sid "${SESSION_ID:-}" \
      --arg engine "$WEB_SEARCH_ENGINE" --argjson count "$count" --argjson uses "$WEB_SEARCH_MAX_USES" \
      --argjson web "$web" --argjson stream "$stream" '
      . as $text | {model:$model,messages:[{role:"user",content:$text}]} +
      (if $sid != "" then {session_id:$sid} else {} end) +
      (if $stream==1 then {stream:true,stream_options:{include_usage:true}} else {} end) +
      (if $web==1 then
        ({tools:[{type:"openrouter:web_search",parameters:({engine:$engine} + (if $count>0 then {max_results:$count} else {} end))}]} +
         (if $uses>0 then {max_tool_calls:$uses} else {} end))
       else {} end)'
  else
    if [ "$web" -eq 1 ]; then
      printf '%s\n' 'Web search in ttychatter.bash requires jq for reliable sources and API errors. Install jq or use ttychatter.python.' >&2
      return 1
    fi
    printf '{"model":"%s","messages":[{"role":"user","content":"%s"}]' "$(printf '%s' "$model" | json_escape_stream)" "$(printf '%s' "$prompt" | json_escape_stream)"
    [ -z "${SESSION_ID:-}" ] || printf ',"session_id":"%s"' "$(printf '%s' "$SESSION_ID" | json_escape_stream)"
    [ "$stream" -eq 0 ] || printf ',"stream":true'
    printf '}\n'
  fi
}

# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function extract_openrouter_text
# Extracts text from OpenRouter's chat-completions response. jq is preferred.
# The sed fallback is intentionally best-effort and documented as such because
# shell regular expressions cannot fully parse JSON.
# ----------------------------------------------------------
extract_openrouter_text() {
  if command -v jq >/dev/null 2>&1; then
    jq -r '
      def content_text($x):
        if ($x|type) == "string" then $x
        elif ($x|type) == "array" then
          [$x[]? | if type == "string" then . elif type == "object" then ((.text? // .content? // empty) | select(type == "string")) else empty end] | join("\n")
        elif ($x|type) == "object" then ((($x.text? // $x.content? // empty) | select(type == "string")))
        else empty end;
      if .error then (.error.message // .error | tostring)
      else ([.choices[]? | content_text(.message.content?), (.text? // empty | select(type == "string"))] | map(select(type == "string" and length > 0)) | join("\n"))
      end'
  else
    tr '\n' ' ' | sed -n 's/^.*"content"[[:space:]]*:[[:space:]]*"\(.*\)"[[:space:]]*[,}].*$/\1/p' | json_unescape_basic
  fi
}

ai_file_leaf() {
  local name="${1:-assistant-file}"
  name="$(basename "$name" 2>/dev/null || printf 'assistant-file')"
  name="$(printf '%s' "$name" | sed -E 's/[^A-Za-z0-9._-]+/-/g; s/^[._-]+//; s/[._-]+$//')"
  [ -n "$name" ] || name="assistant-file"
  printf '%s
' "$(printf '%.96s' "$name")"
}

ai_ext_for_mime() {
  case "$1" in
    application/pdf) printf '.pdf' ;;
    application/json|*json*) printf '.json' ;;
    application/zip|*zip*) printf '.zip' ;;
    *tar*) printf '.tar' ;;
    *gzip*|*gz*) printf '.gz' ;;
    image/png) printf '.png' ;;
    image/jpeg) printf '.jpg' ;;
    image/gif) printf '.gif' ;;
    audio/wav|audio/x-wav) printf '.wav' ;;
    audio/mpeg|audio/mp3) printf '.mp3' ;;
    text/*) printf '.txt' ;;
    *) printf '.bin' ;;
  esac
}

ai_unique_received_path() {
  local name="$1" mime="$2" dir leaf stem ext n path
  dir="$ATTACHMENT_DIR/received"
  mkdir -p "$dir"
  leaf="$(ai_file_leaf "$name")"
  case "$leaf" in *.*) ;; *) leaf="$leaf$(ai_ext_for_mime "$mime")" ;; esac
  path="$dir/$leaf"
  [ ! -e "$path" ] && { printf '%s
' "$path"; return 0; }
  stem="${leaf%.*}"; ext=".${leaf##*.}"
  [ "$stem" = "$leaf" ] && ext=""
  n=1
  while :; do
    path="$(printf '%s/%s-%03d%s' "$dir" "$stem" "$n" "$ext")"
    [ ! -e "$path" ] && { printf '%s
' "$path"; return 0; }
    n=$((n+1))
  done
}

ai_should_download_record() {
  local idx="$1" name="$2" mime="$3" ans
  case "$AI_FILE_DOWNLOAD_MODE" in
    auto|all) return 0 ;;
    ask)
      [ -r /dev/tty ] || return 1
      printf 'AI attachment %s: %s (%s). Download? [y/N] ' "$idx" "$name" "$mime" >/dev/tty
      IFS= read -r ans </dev/tty || return 1
      case "$(printf '%s' "$ans" | tr '[:upper:]' '[:lower:]')" in y|yes|1|true|on) return 0 ;; *) return 1 ;; esac
      ;;
    *) return 1 ;;
  esac
}

ai_save_record() {
  local name="$1" mime="$2" url="$3" data="$4" path meta b64
  path="$(ai_unique_received_path "$name" "$mime")"
  if [ -n "$url" ] && printf '%s' "$url" | grep -q '^data:'; then
    meta="${url%%,*}"; b64="${url#*,}"
    printf '%s' "$b64" | base64 -d > "$path" 2>/dev/null || { rm -f "$path"; return 1; }
    printf '%s
' "$path"; return 0
  fi
  if [ -n "$data" ]; then
    printf '%s' "$data" | base64 -d > "$path" 2>/dev/null || { rm -f "$path"; return 1; }
    printf '%s
' "$path"; return 0
  fi
  if printf '%s' "$url" | grep -Eq '^https?://'; then
    curl -fsSL --max-time 120 "$url" -o "$path" || { rm -f "$path"; return 1; }
    printf '%s
' "$path"; return 0
  fi
  return 1
}

openrouter_sources() {
  jq -r '
    [(.annotations[]?), (.choices[]? | (.message.annotations[]?),(.delta.annotations[]?))]
    | map(select(type=="object" and .type=="url_citation") | (.url_citation // .)
      | select(type=="object") | select((.url|type)=="string") | select(.url|test("^https?://")))
    | unique_by(.url) | to_entries[] | "[\(.key+1)] \((.value.title // .value.url)|tostring|gsub("[\\r\\n\\t]";" ")) - \(.value.url)"'
}

save_openrouter_metadata() {
  local meta
  [ -n "${SESSION:-}" ] && [ -f "$SESSION" ] || return 0
  command -v jq >/dev/null 2>&1 || return 0
  meta="$(jq -c '
    {id,model,usage,provider,service_tier,
      annotations:[(.annotations[]?),(.choices[]? | (.message.annotations[]?),(.delta.annotations[]?))],
      reasoning_details:[.choices[]? | .message.reasoning_details? // .delta.reasoning_details? // empty]}
    | with_entries(select(.value != null and .value != []))' "$1" 2>/dev/null)" || return 0
  [ -z "$meta" ] || [ "$meta" = '{}' ] || printf '[%s] system: openrouter-response: %s\n' "$(timestamp)" "$meta" >> "$SESSION"
}

validate_openrouter_response() {
  if command -v jq >/dev/null 2>&1; then
    jq -e 'if type != "object" then error("expected a JSON object") elif .error then error("OpenRouter API error: " + (.error.message // .error | tostring)) else true end' "$1" >/dev/null
  else
    # This reduced-dependency mode is best effort; jq is recommended.
    if grep -Eq '"error"[[:space:]]*:[[:space:]]*[^n]' "$1"; then
      printf '%s\n' 'OpenRouter reported an error; install jq for full error details.' >&2; return 1
    fi
  fi
}

extract_openrouter_text_and_ai_files() {
  local tmp text records idx name mime url data file_id source loc saved sources
  tmp="$(mktemp "${TMPDIR:-/tmp}/ttychatter.response.XXXXXX")" || return 1
  cat > "$tmp"
  validate_openrouter_response "$tmp" || { rm -f "$tmp"; return 1; }
  save_openrouter_metadata "$tmp"
  text="$(extract_openrouter_text < "$tmp")" || { rm -f "$tmp"; return 1; }
  if command -v jq >/dev/null 2>&1; then
    sources="$(openrouter_sources < "$tmp")"
    [ -z "$sources" ] || text="$text"$'\n\nSources:\n'"$sources"
  fi
  printf '%s
' "$text"
  if [ "$AI_FILE_DOWNLOAD_MODE" = "off" ] || ! command -v jq >/dev/null 2>&1; then
    rm -f "$tmp"; return 0
  fi
  records="$(jq -c '
    def one($o; $paths): reduce $paths[] as $p (null; . // ($o | getpath($p)? | select(type == "string" and length > 0)));
    def looks:
      ((.type? // "") | tostring | ascii_downcase) as $t |
      ($t | test("^(file|output_file|attachment|artifact|image_url|image|audio|video)$"))
      or has("download_url") or has("file_url") or has("content_url") or has("url")
      or has("data") or has("base64") or has("b64_json") or has("file_id")
      or has("image_url") or has("audio") or has("file");
    def rec($src): select(type == "object") | . as $o |
      ((if (.file | type) == "object" then [$o + .file] else [] end)
       + (if (.audio | type) == "object" then [$o + .audio] else [] end)
       + (if (.image_url | type) == "object" then [$o + .image_url] else [] end)
       + [$o])[] |
      select(looks) |
      {name:(one(.; [["filename"],["file_name"],["name"],["title"]]) // "assistant-file"),
       mime:(one(.; [["mime_type"],["mime"],["content_type"],["media_type"]]) // "application/octet-stream"),
       url:(one(.; [["download_url"],["file_url"],["content_url"],["url"],["href"],["image_url","url"]]) // ""),
       data:(one(.; [["data"],["base64"],["b64_json"],["audio","data"]]) // ""),
       file_id:(one(.; [["file_id"],["id"]]) // ""),
       source:$src}
      | select((.url|length) > 0 or (.data|length) > 0 or (.file_id|length) > 0);
    [
      (.choices[]? as $c |
        ($c.message.content[]? | rec("message.content")),
        ($c.message.files[]? | rec("message.files")),
        ($c.message.attachments[]? | rec("message.attachments")),
        ($c.message.output_files[]? | rec("message.output_files")),
        ($c.message.artifacts[]? | rec("message.artifacts")),
        ($c.files[]? | rec("choice.files")),
        ($c.attachments[]? | rec("choice.attachments")),
        ($c.output_files[]? | rec("choice.output_files")),
        ($c.artifacts[]? | rec("choice.artifacts"))
      ),
      (.files[]? | rec("response.files")),
      (.attachments[]? | rec("response.attachments")),
      (.output_files[]? | rec("response.output_files")),
      (.artifacts[]? | rec("response.artifacts"))
    ] | unique | .[]' "$tmp" 2>/dev/null || true)"
  [ -n "$records" ] || { rm -f "$tmp"; return 0; }
  printf '\n[AI file attachments]\n'
  [ "$AI_FILE_DOWNLOAD_MODE" = "ask" ] && [ ! -r /dev/tty ] && printf 'download mode ask is noninteractive here; recording metadata only.\n'
  idx=0
  while IFS= read -r record_json; do
    [ -n "$record_json" ] || continue
    name="$(printf '%s' "$record_json" | jq -r '.name // "assistant-file"')"
    mime="$(printf '%s' "$record_json" | jq -r '.mime // "application/octet-stream"')"
    url="$(printf '%s' "$record_json" | jq -r '.url // ""')"
    data="$(printf '%s' "$record_json" | jq -r '.data // ""')"
    file_id="$(printf '%s' "$record_json" | jq -r '.file_id // ""')"
    source="$(printf '%s' "$record_json" | jq -r '.source // ""')"
    [ -n "$name$url$data$file_id" ] || continue
    idx=$((idx+1))
    if ai_should_download_record "$idx" "$name" "$mime"; then
      if saved="$(ai_save_record "$name" "$mime" "$url" "$data")"; then
        printf 'saved %s: %s (%s) -> %s\n' "$idx" "$name" "$mime" "$saved"
      else
        printf 'not saved %s: %s (%s): download failed\n' "$idx" "$name" "$mime"
      fi
    else
      loc="${url:-${file_id:-embedded data}}"
      printf 'available %s: %s (%s) [%s] %s\n' "$idx" "$name" "$mime" "$source" "$loc"
    fi
  done <<EOF_AI_FILES
$records
EOF_AI_FILES
  rm -f "$tmp"
}

extract_openrouter_stream_piece() {
  jq -j '
    if type != "object" then error("invalid stream event")
    elif .error then error("OpenRouter API error: " + (.error.message // .error | tostring))
    else [.choices[]? | (.delta.content // .message.content // .text // empty)
      | if type=="string" then . elif type=="array" then .[]? | .text? // empty else empty end]
      | map(select(type=="string")) | join("") end'
}

stream_keyboard_watcher() {
  local curl_pid="$1" flag_file="$2" ch
  while kill -0 "$curl_pid" 2>/dev/null; do
    IFS= read -r -s -n 1 -t 0.1 ch </dev/tty || continue
    if [ "$ch" = $'\033' ]; then
      : > "$flag_file"
      kill "$curl_pid" 2>/dev/null || true
      return 0
    fi
  done
}

call_model_stream() {
  local model="$1" prompt="$2" full_prompt payload work fifo err_file flag_file text line data piece curl_pid watcher_pid old_stty status piece_status sources http_code failure="" done_seen=0
  require_api_key || return 1
  full_prompt="$(build_prompt_with_attachments "$prompt")" || return 1
  payload="$(build_openrouter_payload "$(normalize_model_id "$model")" "$full_prompt" 1)" || return 1
  work="$(mktemp -d "${TMPDIR:-/tmp}/ttychatter.stream.XXXXXX")" || return 1
  fifo="$work/events.fifo"; err_file="$work/curl.err"; flag_file="$work/cancelled"
  mkfifo "$fifo" || { rmdir "$work"; return 1; }
  : > "$work/events.jsonl"
  printf '%s' "$payload" > "$work/request.json"
  curl -sS --no-buffer --max-time 120 -D "$work/headers" \
    -H 'Content-Type: application/json' -H "Authorization: Bearer $OPENROUTER_API_KEY" \
    -H 'HTTP-Referer: https://github.com/remfan1994/ttychatter' -H 'X-Title: ttychatter' \
    -d "@$work/request.json" 'https://openrouter.ai/api/v1/chat/completions' >"$fifo" 2>"$err_file" &
  curl_pid=$!
  if [ -t 0 ] && [ -r /dev/tty ]; then
    old_stty="$(stty -g </dev/tty 2>/dev/null || true)"
    [ -z "$old_stty" ] || stty -icanon -echo min 0 time 0 </dev/tty 2>/dev/null || true
    stream_keyboard_watcher "$curl_pid" "$flag_file" & watcher_pid=$!
  else watcher_pid=""; old_stty=""; fi
  text=""
  while IFS= read -r line; do
    line="${line%$'\r'}"
    case "$line" in
      data:*)
        data="${line#data:}"; data="${data# }"
        if [ "$data" = '[DONE]' ]; then done_seen=1; continue; fi
        [ -n "$data" ] || continue
        printf '%s\n' "$data" >> "$work/events.jsonl"
        piece="$(printf '%s' "$data" | extract_openrouter_stream_piece 2>&1; piece_status=$?; printf '.'; exit "$piece_status")"; piece_status=$?
        piece="${piece%.}"
        if [ "$piece_status" -ne 0 ]; then failure="$piece"; kill "$curl_pid" 2>/dev/null || true; break; fi
        if [ -n "$piece" ]; then text="$text$piece"; printf '%s' "$piece" >&2; fi
        ;;
    esac
  done < "$fifo"
  wait "$curl_pid" 2>/dev/null; status=$?
  [ -z "$watcher_pid" ] || { kill "$watcher_pid" 2>/dev/null || true; wait "$watcher_pid" 2>/dev/null || true; }
  [ -z "$old_stty" ] || stty "$old_stty" </dev/tty 2>/dev/null || true
  # Continue beyond finish_reason: the final event may hold usage and citations.
  jq -s '
    reduce .[] as $event ({};
      . * ($event | {id,model,usage,provider,service_tier} | with_entries(select(.value!=null))) |
      .annotations=((.annotations // []) + [$event.annotations[]?,($event.choices[]? | (.message.annotations[]?),(.delta.annotations[]?))]))' \
    "$work/events.jsonl" > "$work/metadata.json" 2>/dev/null || printf '{}' > "$work/metadata.json"
  save_openrouter_metadata "$work/metadata.json"
  sources="$(openrouter_sources < "$work/metadata.json")"
  if [ -n "$sources" ]; then text="$text"$'\n\nSources:\n'"$sources"; printf '\n\nSources:\n%s\n' "$sources" >&2; fi
  if [ -f "$flag_file" ]; then
    case "$text" in *$'\n'|"") ;; *) text="$text"$'\n' ;; esac
    text="$text$STREAM_INTERRUPT_MARKER"; printf '\n%s\n' "$STREAM_INTERRUPT_MARKER" >&2
    status=0
  else
    http_code="$(awk '/^HTTP\// {code=$2} END {print code}' "$work/headers" 2>/dev/null)"
    if [ -z "$failure" ] && [ "$status" -ne 0 ]; then failure="curl error: $(cat "$err_file" 2>/dev/null)"; fi
    case "$http_code" in 2??|'') ;; *) [ -n "$failure" ] || failure="OpenRouter HTTP error $http_code" ;; esac
    if [ -z "$failure" ] && [ "$done_seen" -ne 1 ]; then failure='stream ended before [DONE]; partial reply preserved'; fi
    if [ -z "$failure" ] && [ -z "$text" ]; then failure='OpenRouter returned an empty text response'; fi
    if [ -n "$failure" ]; then
      text="$text"$'\n'"[response error: $failure]"; printf '\n%s\n' "$failure" >&2; status=1
    fi
  fi
  rm -f "$fifo" "$err_file" "$flag_file" "$work/request.json" "$work/events.jsonl" "$work/metadata.json" "$work/headers"
  rmdir "$work" 2>/dev/null || true
  printf '\n' >&2; printf '%s\n' "$text"; return "$status"
}

# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function call_model
# Single OpenRouter generation request. All curl/OpenRouter behavior should
# stay centralized here so endpoint, authentication, timeout, and error changes
# do not get scattered across the interactive loop and colon commands.
# ----------------------------------------------------------
call_model() {
  local model="$1" prompt="$2" full_prompt payload response text status work http_code
  if truthy "$DEMO_MODE"; then demo_ai_response "$prompt"; return 0; fi
  if truthy "$STREAM"; then
    if command -v jq >/dev/null 2>&1; then call_model_stream "$model" "$prompt"; return $?; fi
    printf '%s\n' 'Streaming requires jq for lossless text; using a buffered response.' >&2
  fi
  require_api_key || return 1
  full_prompt="$(build_prompt_with_attachments "$prompt")" || return 1
  payload="$(build_openrouter_payload "$(normalize_model_id "$model")" "$full_prompt")" || return 1
  work="$(mktemp -d "${TMPDIR:-/tmp}/ttychatter.request.XXXXXX")" || return 1
  printf '%s' "$payload" > "$work/request.json"
  http_code="$(curl -sS --max-time 120 -o "$work/response.json" -w '%{http_code}' \
    -H 'Content-Type: application/json' -H "Authorization: Bearer $OPENROUTER_API_KEY" \
    -H 'HTTP-Referer: https://github.com/remfan1994/ttychatter' -H 'X-Title: ttychatter' \
    -d "@$work/request.json" 'https://openrouter.ai/api/v1/chat/completions' 2> "$work/curl.err")"; status=$?
  if [ "$status" -ne 0 ]; then
    printf 'curl error: %s\n' "$(cat "$work/curl.err")" >&2
  else
    case "$http_code" in
      2??) text="$(extract_openrouter_text_and_ai_files < "$work/response.json")"; status=$?
        if [ "$status" -eq 0 ] && [ -z "$text" ]; then printf '%s\n' 'OpenRouter returned an empty text response' >&2; status=1; fi
        [ -z "$text" ] || printf '%s\n' "$text" ;;
      *) printf 'OpenRouter HTTP error %s: %s\n' "$http_code" "$(head -c 4096 "$work/response.json")" >&2; status=1 ;;
    esac
  fi
  rm -f "$work/request.json" "$work/response.json" "$work/curl.err"; rmdir "$work" 2>/dev/null || true
  return "$status"
}

# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function test_model
# User-operated test for one selected model. Do not resurrect automatic model
# probing in this edition; users should choose a model, test it, and decide.
# ----------------------------------------------------------
test_model() { local model="$1"; printf 'Model: %s\nPrompt: %s\n\n' "$model" "$MODEL_TEST_PROMPT"; call_model "$model" "$MODEL_TEST_PROMPT"; }

# ==========================================================
# MEMORY / LOGS / ATTACHMENTS
# ==========================================================

# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function resolve_session_path
# resolve_session_path is part of the dependency-light client behavior.
# Changes should preserve old-system usability and update docs/comments if
# behavior changes.
# ----------------------------------------------------------
resolve_session_path() { printf '%s/%s.log
' "$SESSION_DIR" "$1"; }


# Web search uses OpenRouter's server tool. No local tool execution is needed.
normalize_web_options() {
  case "$WEB_SEARCH_ENGINE" in auto|native|exa|parallel|perplexity|firecrawl) ;; *) WEB_SEARCH_ENGINE=auto ;; esac
  case "$WEB_SEARCH_MAX_RESULTS" in ''|*[!0-9]*) WEB_SEARCH_MAX_RESULTS=5 ;; esac
  case "$WEB_SEARCH_MAX_USES" in ''|*[!0-9]*) WEB_SEARCH_MAX_USES=3 ;; esac
  [ "${#WEB_SEARCH_MAX_RESULTS}" -le 2 ] || WEB_SEARCH_MAX_RESULTS=5
  [ "${#WEB_SEARCH_MAX_USES}" -le 2 ] || WEB_SEARCH_MAX_USES=3
  WEB_SEARCH_MAX_RESULTS=$((10#$WEB_SEARCH_MAX_RESULTS))
  WEB_SEARCH_MAX_USES=$((10#$WEB_SEARCH_MAX_USES))
  [ "$WEB_SEARCH_MAX_RESULTS" -ge 0 ] && [ "$WEB_SEARCH_MAX_RESULTS" -le 25 ] || WEB_SEARCH_MAX_RESULTS=5
  [ "$WEB_SEARCH_MAX_USES" -ge 0 ] && [ "$WEB_SEARCH_MAX_USES" -le 25 ] || WEB_SEARCH_MAX_USES=3
}

web_status() {
  if truthy "$WEB_SEARCH"; then
    printf 'web search: on (engine=%s, results=%s, uses=%s; provider charges may apply)\n' "$WEB_SEARCH_ENGINE" "$WEB_SEARCH_MAX_RESULTS" "$WEB_SEARCH_MAX_USES"
  else
    printf '%s\n' 'web search: off; use :web on to enable'
  fi
}

web_command() {
  case "$1" in
    on|1|yes|true) WEB_SEARCH=1 ;;
    off|0|no|false) WEB_SEARCH=0 ;;
    ''|status) ;;
    *) printf '%s\n' 'usage: :web [on|off|status]'; return 1 ;;
  esac
  normalize_web_options
  web_status
}

# Titles and IDs are separate. A rename never changes request session_id.
manual_session_title_clean() {
  printf '%s' "$1" | LC_ALL=C tr '\r\n\t' '   ' | LC_ALL=C tr -d '\000-\010\013\014\016-\037\177' | awk '{gsub(/[[:space:]]+/," "); sub(/^ /,""); sub(/ $/,""); if(length($0)>200) $0=substr($0,1,200); if(length($0)) print}'
}

absolute_session_path() {
  local dir
  dir="$(cd "$(dirname "$1")" && pwd -P)" || return 1
  printf '%s/%s\n' "$dir" "$(basename "$1")"
}

emit_session_path() { printf 'session: %s\n' "$(absolute_session_path "$SESSION")" >&2; }

session_id_from_file() {
  [ -f "$1" ] || return 1
  LC_ALL=C awk '/^\[[0-9][0-9]:[0-9][0-9]:[0-9][0-9]\] system: session-id: [0-9a-f]+$/ { id=$NF; if(length(id)==32) { print id; found=1; exit } } END { if(!found) exit 1 }' "$1"
}

ensure_session_id() {
  local path="$1" id
  id="$(session_id_from_file "$path" 2>/dev/null)"
  if [ -z "$id" ]; then
    id="$(od -An -N16 -tx1 /dev/urandom 2>/dev/null | tr -d ' \n')"
    [ "${#id}" -eq 32 ] || { printf '%s\n' 'cannot generate a private session ID' >&2; return 1; }
    if [ -s "$path" ] && [ "$(tail -c 1 "$path" | od -An -tu1 | tr -d ' ')" != 10 ]; then printf '\n' >> "$path" || return 1; fi
    printf '[%s] system: session-id: %s\n' "$(timestamp)" "$id" >> "$path" || return 1
  fi
  printf '%s\n' "$id"
}

# Reserve with a hard link so an existing destination is never overwritten.
# Both paths are in the same directory; symlink sources are left untouched.
set_session_title() {
  local path="$1" title="$2" rename_file="${3:-1}" clean id slug dir target old_title old_stem n=0
  clean="$(manual_session_title_clean "$title")"
  [ -n "$clean" ] || { printf '%s\n' 'session name must contain visible text' >&2; return 1; }
  [ -f "$path" ] || { printf 'session not found: %s\n' "$path" >&2; return 1; }
  [ ! -L "$path" ] || { printf '%s\n' 'rename refused: session path is a symbolic link' >&2; return 1; }
  id="$(ensure_session_id "$path")" || return 1
  old_title="$(session_title_from_file "$path" 2>/dev/null || true)"
  old_stem="$(basename "$path" .log)"
  target="$path"
  if [ "$rename_file" -eq 1 ]; then
    slug="$(sanitize_session_name "$clean")"; slug="${slug:0:80}"
    dir="$(dirname "$path")"
    target="$dir/$slug--${id:0:12}.log"
    if [ "$target" != "$path" ]; then
      while ! ln -T "$path" "$target" 2>/dev/null; do
        if [ ! -e "$target" ] && [ ! -L "$target" ]; then
          printf 'cannot rename session to %s; original preserved\n' "$target" >&2; return 1
        fi
        n=$((n+1))
        [ "$n" -le 9999 ] || { printf '%s\n' 'no free session filename' >&2; return 1; }
        target="$(printf '%s/%s--%s-%03d.log' "$dir" "$slug" "${id:0:12}" "$n")"
        [ "$target" = "$path" ] && break
      done
    fi
  fi
  if { [ "$old_stem" != "$clean" ] && ! write_session_alias_metadata "$target" "$old_stem"; } ||
     { [ -n "$old_title" ] && [ "$old_title" != "$clean" ] && [ "$old_title" != "$old_stem" ] && ! write_session_alias_metadata "$target" "$old_title"; }; then
    [ "$target" = "$path" ] || rm -f "$target"
    return 1
  fi
  if ! write_session_title_metadata "$target" "$clean"; then
    [ "$target" = "$path" ] || rm -f "$target"
    return 1
  fi
  if [ "$target" != "$path" ]; then
    if ! rm "$path"; then
      printf 'could not remove old log name; saved log is also at %s\n' "$target" >&2
      return 1
    fi
  fi
  RENAMED_SESSION_PATH="$target"
  if [ "${SESSION:-}" = "$path" ]; then
    SESSION="$target"; SESSION_NAME="$(basename "$target" .log)"; SESSION_ID="$id"
  fi
}

session_needs_name() {
  [ -z "$(session_title_from_file "$SESSION" 2>/dev/null)" ] || return 1
  case "$(basename "$SESSION" .log)" in session|session-[0-9]*|[0-9][0-9][0-9][0-9]-[0-9]*|[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*) return 0 ;; *) return 1 ;; esac
}

maybe_prompt_session_name() {
  truthy "$PROMPT_SESSION_NAME" || return 0
  [ -t 0 ] && [ -t 2 ] || return 0
  [ -z "$PROMPT_TEXT" ] && [ -z "$INPUT_FILE" ] && [ -z "$VOICE_INPUT_PATH" ] || return 0
  session_needs_name || return 0
  local answer
  printf '\nSESSION NAME NEEDED: %s\nName this conversation [Enter to defer]: ' "$(basename "$SESSION")" >&2
  IFS= read -r answer || return 0
  [ -n "$(manual_session_title_clean "$answer")" ] || return 0
  set_session_title "$SESSION" "$answer" "$((1-SESSION_FIXED_PATH))" || return 0
  printf 'Session saved as: %s\n\n' "$SESSION" >&2
}

show_session_name() {
  local title
  title="$(session_title_from_file "$SESSION" 2>/dev/null)"
  if [ -n "$title" ]; then printf 'session name: %s\n' "$title"
  elif session_needs_name; then printf '%s\n' 'SESSION NAME: unnamed. Use :rename Your session name.'
  else printf 'session name: %s\n' "$SESSION_NAME"; fi
}

resolve_session_arg() {
  local name="$1" path fallback result file
  local -a candidates=() matches=()
  case "$name" in
    */*)
      path="${name/#\~/$HOME}"
      [ ! -L "$path" ] || { printf 'session path is a symbolic link: %s\n' "$path" >&2; return 1; }
      printf '%s\n' "$path"; return 0 ;;
    *.log)
      if [ -f "$name" ] || [ -L "$name" ]; then path="$name"; else path="$SESSION_DIR/$name"; fi ;;
    *) path="$SESSION_DIR/$(sanitize_session_name "$name").log" ;;
  esac
  [ ! -L "$path" ] || { printf 'session path is a symbolic link: %s\n' "$path" >&2; return 1; }
  if [ -f "$path" ]; then printf '%s\n' "$path"; return 0; fi
  fallback="$path"
  for file in "$SESSION_DIR"/*.log; do
    [ -f "$file" ] && [ ! -L "$file" ] && [ -r "$file" ] && candidates+=("$file")
  done
  if [ "${#candidates[@]}" -gt 0 ]; then
    result="$(TTYCHATTER_LOOKUP="$name" TTYCHATTER_LOOKUP_STEM="${name%.log}" LC_ALL=C awk '
      function flush() {
        if (file!="" && (alias || title==ENVIRON["TTYCHATTER_LOOKUP"] || title==ENVIRON["TTYCHATTER_LOOKUP_STEM"])) print file;
      }
      FNR==1 { flush(); file=FILENAME; title=""; alias=0; }
      /^\[[0-9][0-9]:[0-9][0-9]:[0-9][0-9]\] system: session-title:/ {
        title=$0; sub(/^\[[^]]*\] system: session-title: ?/,"",title);
      }
      /^\[[0-9][0-9]:[0-9][0-9]:[0-9][0-9]\] system: session-alias:/ {
        value=$0; sub(/^\[[^]]*\] system: session-alias: ?/,"",value);
        if (value==ENVIRON["TTYCHATTER_LOOKUP"] || value==ENVIRON["TTYCHATTER_LOOKUP_STEM"]) alias=1;
      }
      END { flush(); }
    ' "${candidates[@]}")" || return 1
    if [ -n "$result" ]; then
      mapfile -t matches <<< "$result"
      if [ "${#matches[@]}" -ne 1 ]; then
        printf 'ambiguous session name: %s; use a filename from --list\n' "$name" >&2; return 2
      fi
      printf '%s\n' "${matches[0]}"; return 0
    fi
  fi
  printf '%s\n' "$fallback"
}

default_session_name_unique() {
  local base name path n=0
  base="session-$(date +%Y-%m-%d-%H-%M-%S)"
  name="$base"
  while true; do
    path="$(resolve_session_path "$name")"
    if (set -o noclobber; : > "$path") 2>/dev/null; then printf '%s\n' "$name"; return 0; fi
    [ -e "$path" ] || [ -L "$path" ] || { printf 'cannot create session in %s\n' "$SESSION_DIR" >&2; return 1; }
    n=$((n+1)); name="$(printf '%s-%03d' "$base" "$n")"
  done
}

session_title_clean() {
  local max="${2:-8}"
  case "$max" in ''|*[!0-9]*) max=8 ;; esac
  [ "$max" -lt 1 ] && max=8
  printf '%s
' "$1" | tr '
	' '   ' | awk -v max="$max" '
    { gsub(/[[:cntrl:]]/," "); gsub(/[[:space:]]+/," "); sub(/^[[:space:]]+/,""); sub(/[[:space:]]+$/,"");
      lower=tolower($0); if (lower ~ /^session[ -]?title:/ || lower ~ /^title:/) sub(/^[^:]*:[[:space:]]*/,"");
      gsub(/^["`*.-]+/,""); gsub(/["`*.-]+$/,"");
      n=split($0,w," "); out=""; for(i=1;i<=n && i<=max;i++){ if(w[i]!="") out=(out?out" ":"") w[i]; }
      if(length(out)>96) out=substr(out,1,96); sub(/[[:space:]]+$/,"",out); if(out!="") print out; }'
}

session_title_from_file() {
  [ -f "$1" ] || return 1
  LC_ALL=C awk '/^\[[0-9][0-9]:[0-9][0-9]:[0-9][0-9]\] system: session-title:/ { line=$0; sub(/^\[[^]]*\] system: session-title:[[:space:]]*/,"",line); value=line } END { if(value!="") print value; else exit 1 }' "$1"
}

write_session_alias_metadata() {
  local path="$1" alias="$2"
  [ -n "$alias" ] || return 0
  if TTYCHATTER_ALIAS="$alias" LC_ALL=C awk '
    /^\[[0-9][0-9]:[0-9][0-9]:[0-9][0-9]\] system: session-alias:/ {
      value=$0; sub(/^\[[^]]*\] system: session-alias: ?/,"",value);
      if (value==ENVIRON["TTYCHATTER_ALIAS"]) found=1;
    }
    END { exit !found; }
  ' "$path"; then return 0; fi
  printf '[%s] system: session-alias: %s\n' "$(timestamp)" "$alias" >> "$path"
}

write_session_title_metadata() {
  local clean
  clean="$(manual_session_title_clean "$2")" || return 1
  [ -n "$clean" ] || return 1
  printf '[%s] system: session-title: %s\n' "$(timestamp)" "$clean" >> "$1"
}

ensure_prechat_line() {
  local path="$1"
  mkdir -p "$(dirname "$path")"
  if [ ! -s "$path" ]; then
    printf '%s
' "$TC_PRECHAT_LINE" >> "$path"
  fi
}

list_sessions() {
  local f base title shown=0
  mkdir -p "$SESSION_DIR"
  printf '%-42s %s
' "TITLE" "FILE"
  printf '%-42s %s
' "-----" "----"
  for f in "$SESSION_DIR"/*.log; do
    [ -e "$f" ] || continue
    base="$(basename "$f" .log)"
    title="$(session_title_from_file "$f" 2>/dev/null || true)"
    [ -n "$title" ] || title="$base"
    printf '%-42.42s %s
' "$title" "$(basename "$f")"
    shown=1
  done
  [ "$shown" -eq 1 ] || printf '%s
' "no saved sessions"
}
# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function add_context
# Maintains rolling conversation memory for chat continuity.
# ----------------------------------------------------------
add_context() { CONTEXT_BUFFER+=("$1"); local max=$((CONTEXT_TURNS*2)); while [ "${#CONTEXT_BUFFER[@]}" -gt "$max" ]; do CONTEXT_BUFFER=("${CONTEXT_BUFFER[@]:1}"); done; }
# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function build_context
# build_context is part of the dependency-light client behavior. Changes
# should preserve old-system usability and update docs/comments if behavior
# changes.
# ----------------------------------------------------------
build_context() { local item out=""; for item in "${CONTEXT_BUFFER[@]}"; do [ -n "$out" ] && out+=$'\n'; out+="$item"; done; printf '%s' "$out"; }
# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function load_context
# load_context is part of the dependency-light client behavior. Changes
# should preserve old-system usability and update docs/comments if behavior
# changes.
# ----------------------------------------------------------
load_context() {
  [ -f "$SESSION" ] || return 0
  CONTEXT_BUFFER=()
  local entry max_entries=$((CONTEXT_TURNS*2)) tmp
  [ "$max_entries" -ge 2 ] || max_entries=2
  tmp="$(mktemp "${TMPDIR:-/tmp}/ttychatter.memory.XXXXXX")" || return 1
  if command -v jq >/dev/null 2>&1; then
    if ! jq -jRs --argjson limit "$max_entries" --arg notice "$TC_PRECHAT_LINE" '
      def legacy:
        split("\n") | .[1:] | reduce .[] as $line ([];
          if ($line|test("^(User|Assistant): ")) then .+[$line]
          elif length>0 then .[-1]+="\n"+$line else . end);
      (reduce (split("\n")[]) as $line ([];
        if ($line|test("^\\[[0-9]{2}:[0-9]{2}:[0-9]{2}\\] [^:]+:")) then
          .+[$line|capture("^\\[[^]]*\\] (?<speaker>[^:]+): ?(?<text>.*)$")]
        elif length>0 then .[-1].text+="\n"+$line else . end)) |
      reduce .[] as $entry ({context:[],pending:null};
        if ($entry.speaker|ascii_downcase)=="system" then
          if ($entry.text|startswith("CONTEXT_SNAPSHOT_JSON ")) then
            (try ($entry.text|ltrimstr("CONTEXT_SNAPSHOT_JSON ")|fromjson) catch null) as $snapshot |
            if ($snapshot|type)=="array" and all($snapshot[];type=="string") then .context=$snapshot | .pending=null else . end
          elif ($entry.text|test("^CONTEXT_BEGIN(\\n|$)")) then .pending=($entry.text|legacy)
          elif ($entry.text|test("^CONTEXT_END\\s*$")) and .pending!=null then .context=.pending | .pending=null
          else . end
        elif $entry.speaker=="remfan1994" and ($entry.text|rtrimstr("\n"))==$notice then .
        else .pending=null | .context += [(if (["AI","OpenRouter","Assistant"]|index($entry.speaker))!=null then "Assistant: " else "User: " end)+($entry.text|rtrimstr("\n"))]
        end | .context=.context[-$limit:]) | .context[] | .+"\u0000"' "$SESSION" > "$tmp"; then
      rm -f "$tmp"; printf '%s\n' 'Could not restore session context.' >&2; return 1
    fi
  else
    # Refuse to bypass an intentional JSON memory clear/edit without a parser.
    if grep -Eq '^\[[0-9][0-9]:[0-9][0-9]:[0-9][0-9]\] system: CONTEXT_SNAPSHOT_JSON ' "$SESSION"; then
      rm -f "$tmp"; printf '%s\n' 'This session uses JSON memory snapshots. Install jq to resume its saved memory accurately.' >&2; return 1
    fi
    awk -v max="$max_entries" -v notice="$TC_PRECHAT_LINE" '
      function flush( role,i,linecount,lines,line) {
        if(speaker=="system") {
          if(content=="CONTEXT_BEGIN" || index(content,"CONTEXT_BEGIN\n")==1) {
            pending=1; sn=0; linecount=split(content,lines,"\n");
            for(i=2;i<=linecount;i++) {
              line=lines[i]; if(line ~ /^(User|Assistant): /) snapshot[++sn]=line;
              else if(sn>0) snapshot[sn]=snapshot[sn] "\n" line;
            }
          } else if(content=="CONTEXT_END" && pending) {
            n=0; for(i=1;i<=sn;i++) entries[++n]=snapshot[i]; pending=0;
          }
        } else if(speaker!="" && content!="" && !(speaker=="remfan1994" && content==notice)) {
          pending=0; role=(speaker=="AI" || speaker=="OpenRouter" || speaker=="Assistant")?"Assistant":"User";
          entries[++n]=role ": " content;
        }
        while(n>max) { for(i=2;i<=n;i++) entries[i-1]=entries[i]; n--; }
        speaker=""; content="";
      }
      /^\[[0-9][0-9]:[0-9][0-9]:[0-9][0-9]\] [^:]+:/ {
        flush(); line=$0; sub(/^\[[^]]*\] /,"",line); speaker=line; sub(/:.*/,"",speaker); sub(/^[^:]*:[[:space:]]*/,"",line); content=line; next;
      }
      { if(speaker!="") content=content "\n" $0 }
      END { flush(); for(i=1;i<=n;i++) printf "%s%c",entries[i],0; }' "$SESSION" > "$tmp" || { rm -f "$tmp"; return 1; }
  fi
  while IFS= read -r -d '' entry; do CONTEXT_BUFFER+=("$entry"); done < "$tmp"
  rm -f "$tmp"
}
# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function write_context_snapshot
# write_context_snapshot is part of the dependency-light client behavior.
# Changes should preserve old-system usability and update docs/comments if
# behavior changes.
# ----------------------------------------------------------
write_context_snapshot() {
  local item ts="$1" snapshot
  if command -v jq >/dev/null 2>&1; then
    snapshot="$(if [ "${#CONTEXT_BUFFER[@]}" -gt 0 ]; then printf '%s\0' "${CONTEXT_BUFFER[@]}"; fi | jq -cRs 'if .=="" then [] else split("\u0000")[:-1] end')" || return 1
    printf '[%s] system: CONTEXT_SNAPSHOT_JSON %s\n' "$ts" "$snapshot" >> "$SESSION"
  else
    printf '[%s] system: CONTEXT_BEGIN\n' "$ts" >> "$SESSION" || return 1
    for item in "${CONTEXT_BUFFER[@]}"; do printf '%s\n' "$item" >> "$SESSION" || return 1; done
    printf '[%s] system: CONTEXT_END\n' "$ts" >> "$SESSION"
  fi
}
# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function extract_code_blocks
# Saves generated fenced code to files when possible, keeping terminal
# output readable.
# ----------------------------------------------------------
extract_code_blocks() {
  local line opening="" content="" inblock=0 lines=0 lang ext tag path n
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" == '```'* ]]; then
      if [ "$inblock" -eq 0 ]; then
        opening="$line"; lang="${line#'```'}"; content=""; lines=0; inblock=1
      else
        if [ "$lines" -ge "$CODE_ATTACHMENT_MIN_LINES" ]; then
          mkdir -p "$ATTACHMENT_DIR" || return 1
          tag="$(printf '%s' "${lang:-text}" | LC_ALL=C tr -c 'A-Za-z0-9_+-' '_')"
          case "$lang" in sh|bash|shell) ext=.sh ;; python|py) ext=.py ;; json) ext=.json ;; yaml|yml) ext=.yml ;; javascript|js) ext=.js ;; html|css|sql|c|h) ext=".$lang" ;; cpp|c++) ext=.cpp ;; *) ext=.txt ;; esac
          n=1
          while true; do
            path="$(printf '%s/%s-%s-%02d%s' "$ATTACHMENT_DIR" "$SESSION_NAME" "$tag" "$n" "$ext")"
            if (set -o noclobber; printf '%s' "$content" > "$path") 2>/dev/null; then break; fi
            [ -e "$path" ] || [ -L "$path" ] || { printf 'Cannot write code attachment: %s\n' "$path" >&2; return 1; }
            n=$((n+1)); [ "$n" -le 99999 ] || return 1
          done
          printf '\n[code attachment saved]\nfile: %s\nlocation: %s\n' "$(basename "$path")" "$ATTACHMENT_DIR"
        else
          printf '%s\n%s%s\n' "$opening" "$content" "$line"
        fi
        inblock=0; content=""
      fi
    elif [ "$inblock" -eq 1 ]; then
      content="$content$line"$'\n'
      [[ "$line" =~ [^[:space:]] ]] && lines=$((lines+1))
    else
      printf '%s\n' "$line"
    fi
  done
  if [ "$inblock" -eq 1 ]; then printf '%s\n%s' "$opening" "$content"; fi
  return 0
}



# ----------------------------------------------------------
# MAINTAINER COMMENTARY: model favorites in bash-only
# The dependency-light edition still needs persistent model bookmarks.  We use
# a plain one-id-per-line file so this feature works without Python or jq.  The
# format is intentionally boring: it can be edited with sed, cat, vi, or any
# ancient text tool available on old systems.
# ----------------------------------------------------------
ensure_favorites_dir() {
  mkdir -p "$(dirname "$MODEL_FAVORITES_FILE")" 2>/dev/null || true
}

list_favorite_models() {
  [ -f "$MODEL_FAVORITES_FILE" ] && grep -v '^[[:space:]]*$' "$MODEL_FAVORITES_FILE" || true
}

favorite_model_command() {
  local model="$1"
  [ -n "$model" ] || { printf 'ERROR: model id required.\n' >&2; return 2; }
  ensure_favorites_dir
  touch "$MODEL_FAVORITES_FILE"
  if ! grep -Fxq "$model" "$MODEL_FAVORITES_FILE" 2>/dev/null; then
    printf '%s\n' "$model" >> "$MODEL_FAVORITES_FILE"
  fi
  printf 'favorited model: %s\n' "$model"
}

unfavorite_model_command() {
  local model="$1" tmp
  [ -n "$model" ] || { printf 'ERROR: model id required.\n' >&2; return 2; }
  [ -f "$MODEL_FAVORITES_FILE" ] || return 0
  tmp="$(mktemp "${MODEL_FAVORITES_FILE}.tmp.XXXXXX")"
  grep -Fxv "$model" "$MODEL_FAVORITES_FILE" > "$tmp" || true
  mv "$tmp" "$MODEL_FAVORITES_FILE"
  printf 'unfavorited model: %s\n' "$model"
}


# ----------------------------------------------------------
# MAINTAINER COMMENTARY: bash-only style output
# Theme support in a shell client is necessarily modest.  The goal is parity
# with user configuration, not a fake curses renderer.  We only emit ANSI
# styling when stdout is an interactive terminal.  This protects loopback and
# pipeline users from receiving escape sequences in machine-consumed output.
# ----------------------------------------------------------
ai_style_start() { [ -t 1 ] || return 0; case "$THEME" in dim|default|solarized_dark) printf '[2m';; high_contrast) printf '[1m';; mono) ;; esac; }
ai_style_end() { [ -t 1 ] && [ "$THEME" != "mono" ] && printf '[0m' || true; }
print_ai_output() { ai_style_start; printf '%s

' "$1"; ai_style_end; }

# ==========================================================
# RUNTIME COLON-COMMAND MODE
# ----------------------------------------------------------
# The bash-only edition needs a way to expose client operations while the chat
# loop is running. Ncurses can use F-key screens; this edition deliberately has
# no such UI layer. Colon commands are the dependency-light equivalent. They
# are local client commands, not AI messages. They are not logged, not sent
# to the model, and not stored in memory.
#
# This mechanism is essential to keeping the bash-only edition from becoming
# a mere curl wrapper. Users can manage sessions, models, memory, attachments,
# editor workflow, and config without quitting the client.
# ==========================================================

runtime_command_help() {
  cat <<EOF
Runtime commands are local ttychatter.bash commands.
They are not sent to OpenRouter.

General:
  :help                   Show this menu
  :quit                   Exit chat
  :credits                Show credits
  :doctor                 Run diagnostics

Sessions:
  :list                   List saved sessions with friendly titles
  :rename TITLE           Name the session and rename its log file

Models:
  :models                 Show model list
  :update-models          Refresh model cache from OpenRouter
  :select-model           Numbered model selector
  :test-model MODEL       Test one model
  :model MODEL            Use MODEL for this running chat only
  :model-save MODEL       Use MODEL and save MODEL=... to config
  :favorites              List favorite/bookmarked models
  :favorite MODEL         Add MODEL to favorites
  :unfavorite MODEL       Remove MODEL from favorites

Config and API key:
  :config                 Print config summary
  :set KEY VALUE          Save config value
  :unset KEY              Remove config key
  :set-api-key            Prompt and save API key
  :forget-api-key         Remove API key from config

Memory:
  :memory                 Show current memory/context
  :clear-memory           Clear current memory/context

Files and editor:
  :attach FILE            Attach FILE to next message
  :attachments            List saved attachments
  :editor                 Compose a prompt in VISUAL/EDITOR/vi/nano and send it
  :turns                  List message numbers
  :branch N               Branch current session after message N
  :search TEXT            Search current session
  :search-all TEXT        Search all saved sessions
  :web [on|off|status]                 Show/toggle OpenRouter web search
  :stream on|off          Toggle requested streaming mode
  :ai-download MODE       AI file receipt: ask, auto, metadata, off
  :status on|off          Toggle local status updates
  :voice FILE             Transcribe FILE with VOICE_INPUT_CMD and send
  :speak on|off|last      Toggle/speak with VOICE_OUTPUT_CMD

Use Ctrl+D after a colon command, just as you do for chat messages.
Type \: at the beginning if you really want to send a message beginning with :.
EOF
}

print_config_summary() {
  printf 'config file: %s\n' "$CONFIG_FILE"
  printf 'MODEL=%s\n' "${MODEL:-}"
  printf 'CONTEXT_TURNS=%s\n' "$CONTEXT_TURNS"
  printf 'SESSION_DIR=%s\n' "$SESSION_DIR"
  printf 'ATTACHMENT_DIR=%s\n' "$ATTACHMENT_DIR"
  printf 'MODEL_TEST_PROMPT=%s\n' "$MODEL_TEST_PROMPT"
  printf 'STARTUP_NOTICE=%s\n' "$STARTUP_NOTICE"
  printf 'SESSION_AUTO_TITLE=%s\n' "$SESSION_AUTO_TITLE"
  printf 'SESSION_TITLE_MODEL=%s\n' "$SESSION_TITLE_MODEL"
  printf 'SESSION_TITLE_MAX_WORDS=%s\n' "$SESSION_TITLE_MAX_WORDS"
  printf 'WEB_SEARCH=%s\nWEB_SEARCH_ENGINE=%s\nWEB_SEARCH_MAX_RESULTS=%s\nWEB_SEARCH_MAX_USES=%s\nPROMPT_SESSION_NAME=%s\n' "$WEB_SEARCH" "$WEB_SEARCH_ENGINE" "$WEB_SEARCH_MAX_RESULTS" "$WEB_SEARCH_MAX_USES" "$PROMPT_SESSION_NAME"
  printf 'STREAM=%s\n' "$STREAM"
  printf 'STATUS_UPDATES=%s\n' "$STATUS_UPDATES"
  printf 'VOICE_INPUT_CMD=%s\n' "$VOICE_INPUT_CMD"
  printf 'VOICE_OUTPUT_CMD=%s\n' "$VOICE_OUTPUT_CMD"
  printf 'VOICE_OUTPUT=%s\n' "$VOICE_OUTPUT"
  printf 'AI_FILE_DOWNLOAD_MODE=%s\n' "$AI_FILE_DOWNLOAD_MODE"
  printf 'THEME=%s\n' "$THEME"
  printf 'SEND_INPUT=%s\n' "$SEND_INPUT"
  printf 'CODE_ATTACHMENT_MIN_LINES=%s\n' "$CODE_ATTACHMENT_MIN_LINES"
  printf 'MODEL_CACHE_FILE=%s\n' "$MODEL_CACHE_FILE"
  printf 'MODEL_FAVORITES_FILE=%s\n' "$MODEL_FAVORITES_FILE"
  printf 'MODEL_SORT_ORDER=%s\n' "$MODEL_SORT_ORDER"
  printf 'MODEL_MIN_INPUT_TOKENS=%s\n' "$MODEL_MIN_INPUT_TOKENS"
  printf 'MODEL_MIN_OUTPUT_TOKENS=%s\n' "$MODEL_MIN_OUTPUT_TOKENS"
  if [ -n "$OPENROUTER_API_KEY" ]; then printf 'OPENROUTER_API_KEY=(loaded)\n'; else printf 'OPENROUTER_API_KEY=(not set)\n'; fi
}

editor_command_basic() {
  if [ -n "${VISUAL:-}" ]; then printf '%s\n' "$VISUAL"; return 0; fi
  if [ -n "${EDITOR:-}" ]; then printf '%s\n' "$EDITOR"; return 0; fi
  command -v nano >/dev/null 2>&1 && { printf '%s\n' "nano"; return 0; }
  command -v vi >/dev/null 2>&1 && { printf '%s\n' "vi"; return 0; }
  printf '%s\n' "vi"
}

compose_with_editor_basic() {
  local tmp editor
  tmp="$(mktemp)" || return 1
  editor="$(editor_command_basic)"
  $editor "$tmp"
  cat "$tmp"
  rm -f "$tmp"
}

rename_current_session() {
  [ -n "$1" ] || { printf '%s\n' 'usage: :rename Your session name'; return 1; }
  set_session_title "$SESSION" "$1" || return 1
  printf 'session name: %s\nsession: %s\n' "$(session_title_from_file "$SESSION")" "$SESSION"
}

rename_session_command() {
  local target="$1" title="$2" path
  [ -n "$target" ] && [ -n "$title" ] || { printf '%s\n' 'ERROR: --rename-session requires SESSION TITLE' >&2; return 2; }
  path="$(resolve_session_arg "$target")" || return 1
  set_session_title "$path" "$title" || return 1
  printf 'session name: %s\nsession: %s\n' "$(session_title_from_file "$RENAMED_SESSION_PATH")" "$(absolute_session_path "$RENAMED_SESSION_PATH")"
}

show_current_memory_runtime() {
  local item
  [ "${#CONTEXT_BUFFER[@]}" -eq 0 ] && { printf '%s\n' "current memory is empty"; return 0; }
  printf '%s\n' "current memory/context buffer:"
  printf '%s\n' "----------------------------------------"
  for item in "${CONTEXT_BUFFER[@]}"; do printf '%s\n' "$item"; done
  printf '%s\n' "----------------------------------------"
}

list_attachments_runtime() {
  local file
  [ -d "$ATTACHMENT_DIR" ] || { printf 'attachment directory missing: %s\n' "$ATTACHMENT_DIR"; return 1; }
  for file in "$ATTACHMENT_DIR"/*; do
    [ -e "$file" ] || { printf 'no attachments found in %s\n' "$ATTACHMENT_DIR"; return 0; }
    basename "$file"
  done
}

apply_runtime_config_change() {
  local key="$1" value="$2"
  case "$key" in
    MODEL) MODEL="$(normalize_model_id "$value")"; if truthy "$DEMO_MODE" && [ -z "$MODEL" ]; then MODEL="demo/auto"; fi
ACTIVE_MODEL="$(effective_model)" ;;
    CONTEXT_TURNS|HISTORY_LIMIT) CONTEXT_TURNS="$value" ;;
    SESSION_DIR) SESSION_DIR="${value/#\~/$HOME}"; mkdir -p "$SESSION_DIR" ;;
    ATTACHMENT_DIR) ATTACHMENT_DIR="${value/#\~/$HOME}"; mkdir -p "$ATTACHMENT_DIR" ;;
    AI_FILE_DOWNLOAD_MODE|AI_DOWNLOAD) AI_FILE_DOWNLOAD_MODE="$value" ;;
    WEB_SEARCH) WEB_SEARCH="$value" ;;
    WEB_SEARCH_ENGINE) WEB_SEARCH_ENGINE="$value" ;;
    WEB_SEARCH_MAX_RESULTS) WEB_SEARCH_MAX_RESULTS="$value" ;;
    WEB_SEARCH_MAX_USES) WEB_SEARCH_MAX_USES="$value" ;;
    PROMPT_SESSION_NAME) PROMPT_SESSION_NAME="$value" ;;
    MODEL_TEST_PROMPT) MODEL_TEST_PROMPT="$value" ;;
    STREAM) STREAM="$value" ;;
    SESSION_AUTO_TITLE) SESSION_AUTO_TITLE="$value" ;;
    SESSION_TITLE_MODEL) SESSION_TITLE_MODEL="$value" ;;
    SESSION_TITLE_MAX_WORDS) SESSION_TITLE_MAX_WORDS="$value" ;;
    STARTUP_NOTICE) STARTUP_NOTICE="$value" ;;
    THEME) THEME="$value" ;;
    SEND_INPUT) SEND_INPUT="$value" ;;
    CODE_ATTACHMENT_MIN_LINES) CODE_ATTACHMENT_MIN_LINES="$value" ;;
    MODEL_FAVORITES_FILE) MODEL_FAVORITES_FILE="${value/#\~/$HOME}" ;;
    MODEL_SORT_ORDER) MODEL_SORT_ORDER="$value" ;;
    MODEL_MIN_INPUT_TOKENS) MODEL_MIN_INPUT_TOKENS="$value" ;;
    MODEL_MIN_OUTPUT_TOKENS) MODEL_MIN_OUTPUT_TOKENS="$value" ;;
    MODEL_FILTER_REQUIRE_TOKENS) MODEL_FILTER_REQUIRE_TOKENS="$value" ;;
  esac
}


status_update() {
  truthy "$STATUS_UPDATES" || return 0
  printf '[%s] status: %s\n' "$(timestamp)" "$*" >&2
}

shell_quote() { printf '%q' "$1"; }

command_with_file_placeholder() {
  local template="$1" file="$2" q
  q="$(shell_quote "$file")"
  case "$template" in *%f*) printf '%s\n' "${template//%f/$q}" ;; *) printf '%s %s\n' "$template" "$q" ;; esac
}

run_voice_input_command() {
  local path="$1" cmd out
  [ -n "$path" ] || { printf '%s\n' "ERROR: --voice-input requires a file" >&2; return 2; }
  [ -n "$VOICE_INPUT_CMD" ] || { printf '%s\n' "ERROR: VOICE_INPUT_CMD is not configured" >&2; return 1; }
  cmd="$(command_with_file_placeholder "$VOICE_INPUT_CMD" "$path")"
  out="$(eval "$cmd")" || return 1
  printf '%s\n' "$out"
}

run_voice_output_command() {
  local text="$1"
  [ -n "$VOICE_OUTPUT_CMD" ] || { printf '%s\n' "ERROR: VOICE_OUTPUT_CMD/TTS_CMD is not configured" >&2; return 1; }
  printf '%s\n' "$text" | eval "$VOICE_OUTPUT_CMD"
}

generate_session_title() {
  local user_text="$1" ai_text="$2" prompt title saved saved_stream saved_web
  truthy "$SESSION_AUTO_TITLE" || return 0
  [ -n "$(session_title_from_file "$SESSION" 2>/dev/null || true)" ] && return 0
  if truthy "$DEMO_MODE"; then
    title="$(session_title_clean "$user_text" "$SESSION_TITLE_MAX_WORDS")"
  else
    [ -n "$OPENROUTER_API_KEY" ] || return 0
    [ -n "$SESSION_TITLE_MODEL" ] || return 0
    prompt="Generate a concise topical title for this ttychatter session. Reply with only the title, no quotes, no explanation, maximum $SESSION_TITLE_MAX_WORDS words.\n\nUser: $user_text\nAssistant: $ai_text"
    saved=("${ATTACH_FILES[@]}")
    saved_stream="$STREAM"; saved_web="$WEB_SEARCH"; STREAM=0; WEB_SEARCH=0
    ATTACH_FILES=()
    title="$(call_model "$SESSION_TITLE_MODEL" "$prompt" 2>/dev/null | head -n 1)"
    ATTACH_FILES=("${saved[@]}")
    STREAM="$saved_stream"; WEB_SEARCH="$saved_web"
    title="$(session_title_clean "$title" "$SESSION_TITLE_MAX_WORDS")"
  fi
  if [ -n "$title" ]; then set_session_title "$SESSION" "$title" "$((1-SESSION_FIXED_PATH))"; fi
}

turns_command() {
  local path="$1"
  path="$(resolve_session_arg "$path")" || return 1
  [ -f "$path" ] || { printf 'session not found: %s\n' "$path" >&2; return 1; }
  printf '%-5s %-10s %s\n' "MSG" "ROLE" "PREVIEW"
  TTYCHATTER_NOTICE="$TC_PRECHAT_LINE" awk '
    /^\[[0-9][0-9]:[0-9][0-9]:[0-9][0-9]\] / {
      line=$0; sub(/^\[[^]]*\] /,"",line); speaker=line; sub(/:.*/,"",speaker);
      if (tolower(speaker)=="system") next;
      role=(speaker=="AI" || speaker=="OpenRouter" || speaker=="Assistant") ? "assistant" : "user";
      content=line; sub(/^[^:]*: ?/,"",content);
      if (speaker=="remfan1994" && content==ENVIRON["TTYCHATTER_NOTICE"]) next;
      gsub(/[[:space:]]+/," ",content);
      n++; if(length(content)>78) content=substr(content,1,75)"...";
      printf "%-5d %-10s %s\n", n, role, content;
    }' "$path"
}

branch_session_command() {
  local source="$1" turn="$2" src branch title
  [ -n "$source" ] && [ -n "$turn" ] || { printf '%s\n' "usage: --branch SESSION TURN" >&2; return 2; }
  case "$turn" in ''|*[!0-9]*) printf '%s\n' 'branch turn must be numeric' >&2; return 2 ;; esac
  src="$(resolve_session_arg "$source")" || return 1
  [ -f "$src" ] || { printf 'session not found: %s\n' "$src" >&2; return 1; }
  branch="$(resolve_session_path "$(default_session_name_unique)")"
  ensure_prechat_line "$branch"
  printf '[%s] system: branch-from: %s\n[%s] system: branch-turn: %s\n[%s] system: branch-reason: manual-branch\n' "$(timestamp)" "$src" "$(timestamp)" "$turn" "$(timestamp)" >> "$branch"
  TTYCHATTER_NOTICE="$TC_PRECHAT_LINE" awk -v max="$turn" '
    function ismsg(line, speaker, content) {
      if (line !~ /^\[[0-9][0-9]:[0-9][0-9]:[0-9][0-9]\] /) return 0;
      sub(/^\[[^]]*\] /,"",line); speaker=line; sub(/:.*/,"",speaker);
      content=line; sub(/^[^:]*: ?/,"",content);
      return !(tolower(speaker)=="system" || (speaker=="remfan1994" && content==ENVIRON["TTYCHATTER_NOTICE"]));
    }
    /^\[[0-9][0-9]:[0-9][0-9]:[0-9][0-9]\] / { if(ismsg($0)) { count++; keep=(count<=max) } else keep=0 }
    { if (keep && count<=max) print }
  ' "$src" >> "$branch"
  title="$(session_title_from_file "$src" 2>/dev/null || basename "$src" .log)"
  set_session_title "$branch" "Branch of $title" || return 1
  printf '%s\n' "$RENAMED_SESSION_PATH"
}

export_session_command() {
  local name="$1" fmt="${2:-markdown}" out="$3" path title tmp status
  [ -n "$name" ] || { printf '%s\n' "usage: --export SESSION [--format markdown|text] [--output FILE]" >&2; return 2; }
  path="$(resolve_session_arg "$name")" || return 1
  [ -f "$path" ] || { printf 'session not found: %s\n' "$path" >&2; return 1; }
  title="$(session_title_from_file "$path" 2>/dev/null || basename "$path" .log)"
  tmp="$(mktemp)" || return 1
  case "$fmt" in
    text)
      printf 'Title: %s\nSession: %s\n\n' "$title" "$path" > "$tmp"
      ;;
    json)
      printf '%s\n' "bash-only JSON export is intentionally omitted; use ttychatter.python or the C edition." >&2; rm -f "$tmp"; return 2 ;;
    *)
      printf '# %s\n\nSource: `%s`\n\n' "$title" "$path" > "$tmp"
      ;;
  esac
  TTYCHATTER_NOTICE="$TC_PRECHAT_LINE" awk -v fmt="$fmt" '
    function flush(    label) {
      if (speaker=="" || tolower(speaker)=="system") return;
      if (speaker=="remfan1994" && content==ENVIRON["TTYCHATTER_NOTICE"]) return;
      label=(speaker=="AI" || speaker=="OpenRouter" || speaker=="Assistant") ? "Assistant" : "User";
      if (fmt=="text") print label ":\n" content "\n";
      else print "## " label "\n\n" content "\n";
    }
    /^\[[0-9][0-9]:[0-9][0-9]:[0-9][0-9]\] [^:]+:/ {
      flush(); line=$0; sub(/^\[[^]]*\] /,"",line); speaker=line; sub(/:.*/,"",speaker);
      sub(/^[^:]*: ?/,"",line); content=line; next;
    }
    { if (speaker!="") content=content "\n" $0; }
    END { flush(); }
  ' "$path" >> "$tmp"
  status=$?
  if [ "$status" -eq 0 ]; then
    if [ -n "$out" ]; then cat "$tmp" > "$out"; else cat "$tmp"; fi
    status=$?
  fi
  rm -f "$tmp"
  return "$status"
}

search_session_file() {
  local file="$1" query="$2"
  [ -f "$file" ] || { printf 'no such session log: %s\n' "$file" >&2; return 1; }
  grep -in -- "$query" "$file" 2>/dev/null | sed "s#^#$(basename "$file"):#" || true
}

search_all_sessions() {
  local query="$1" file found=0
  for file in "$SESSION_DIR"/*.log; do
    [ -e "$file" ] || continue
    if grep -iq -- "$query" "$file" 2>/dev/null; then
      found=1
      printf '%s\n' "--- $(basename "$file") ---"
      grep -in -- "$query" "$file" 2>/dev/null || true
    fi
  done
  [ "$found" -eq 1 ] || printf 'no matches for: %s\n' "$query"
}

search_command() {
  local query="$1" all="$2" target="${3:-}"
  [ -n "$query" ] || { printf '%s\n' "ERROR: --search requires text" >&2; return 2; }
  if [ "$all" -eq 1 ]; then
    search_all_sessions "$query"
  else
    [ -n "$target" ] || target="$SESSION"
    search_session_file "$target" "$query"
  fi
}

edit_turn_command() {
  printf '%s\n' "--edit-turn is not implemented in ttychatter.bash; use ttychatter.python or the C edition for edit-and-branch." >&2
  return 2
}

handle_runtime_command() {
  local raw="$1" command rest key value editor_buffer
  case "$raw" in
    \\:*) RUNTIME_SEND_OVERRIDE="${raw#\\}"; return 2 ;;
    :*) ;;
    *) return 1 ;;
  esac
  raw="${raw#:}"
  command="${raw%%[[:space:]]*}"
  if [ "$command" = "$raw" ]; then rest=""; else rest="${raw#*[[:space:]]}"; fi

  case "$command" in
    ""|help|h|\?) runtime_command_help ;;
    quit|exit) printf '%s\n' "exiting"; exit 0 ;;
    credits) credits ;;
    doctor) doctor_command ;;
    list|sessions) list_sessions ;;
    rename) rename_current_session "$rest" ;;
    web) web_command "$rest" ;;
    turns) turns_command "$SESSION_NAME" ;;
    branch|fork) branch_session_command "$SESSION_NAME" "$rest" ;;
    search) [ -n "$rest" ] && search_command "$rest" 0 "$SESSION" || printf '%s
' "usage: :search TEXT" ;;
    search-all|search_all) [ -n "$rest" ] && search_command "$rest" 1 "$SESSION" || printf '%s
' "usage: :search-all TEXT" ;;
    stream) case "$rest" in on|1|yes|true) STREAM=1; printf '%s
' "streaming requested" ;; off|0|no|false) STREAM=0; printf '%s
' "streaming disabled" ;; *) printf '%s
' "usage: :stream on|off" ;; esac ;;
    ai-download|ai-files) case "$rest" in ask|auto|metadata|off) AI_FILE_DOWNLOAD_MODE="$rest"; printf 'AI file download mode: %s
' "$AI_FILE_DOWNLOAD_MODE" ;; *) printf '%s
' "usage: :ai-download ask|auto|metadata|off" ;; esac ;;
    status|progress) case "$rest" in on|1|yes|true) STATUS_UPDATES=1; printf '%s
' "status updates enabled" ;; off|0|no|false) STATUS_UPDATES=0; printf '%s
' "status updates disabled" ;; *) printf '%s
' "usage: :status on|off" ;; esac ;;
    voice|transcribe) [ -n "$rest" ] && { voice_text="$(run_voice_input_command "$rest")" && send_one_message "$voice_text"; } || printf '%s
' "usage: :voice FILE" ;;
    speak) case "$rest" in on) SPEAK_OVERRIDE=1; printf '%s
' "voice output enabled" ;; off) SPEAK_OVERRIDE=0; printf '%s
' "voice output disabled" ;; last) [ -n "${LAST_AI_OUTPUT:-}" ] && run_voice_output_command "$LAST_AI_OUTPUT" || printf '%s
' "no previous AI output" ;; *) printf '%s
' "usage: :speak on|off|last" ;; esac ;;
    models) list_models ;;
    routers) MODEL_TYPE_FILTER="routers"; list_models ;;
    update-models|update_models) update_models_cache ;;
    select-model|select_model) select_model ;;
    test-model|test_model) [ -n "$rest" ] && test_model "$rest" || printf '%s\n' "usage: :test-model MODEL" ;;
    model) [ -n "$rest" ] && { MODEL="$(normalize_model_id "$rest")"; if truthy "$DEMO_MODE" && [ -z "$MODEL" ]; then MODEL="demo/auto"; fi
ACTIVE_MODEL="$(effective_model)"; printf 'active model: %s\n' "$ACTIVE_MODEL"; } || printf 'active model: %s\n' "$(model_label)" ;;
    model-save) [ -n "$rest" ] && { MODEL="$(normalize_model_id "$rest")"; if truthy "$DEMO_MODE" && [ -z "$MODEL" ]; then MODEL="demo/auto"; fi
ACTIVE_MODEL="$(effective_model)"; save_config_value MODEL "$ACTIVE_MODEL"; printf 'saved MODEL=%s\n' "$ACTIVE_MODEL"; } || printf '%s\n' "usage: :model-save MODEL" ;;
    config) print_config_summary ;;
    set)
      key="${rest%%[[:space:]]*}"; if [ "$key" = "$rest" ]; then value=""; else value="${rest#*[[:space:]]}"; fi
      [ -n "$key" ] || { printf '%s\n' "usage: :set KEY VALUE"; return 0; }
      save_config_value "$key" "$value"; apply_runtime_config_change "$key" "$value"; printf 'set %s=%s\n' "$key" "$value"
      ;;
    unset) [ -n "$rest" ] && { remove_config_key "$rest"; [ "$rest" = "MODEL" ] && { MODEL=""; if truthy "$DEMO_MODE" && [ -z "$MODEL" ]; then MODEL="demo/auto"; fi
ACTIVE_MODEL="$(effective_model)"; }; printf 'removed config key if present: %s\n' "$rest"; } || printf '%s\n' "usage: :unset KEY" ;;
    set-api-key|api-key) set_api_key_command ;;
    forget-api-key) forget_api_key_command ;;
    memory|show-memory) show_current_memory_runtime ;;
    clear-memory) CONTEXT_BUFFER=(); write_context_snapshot "$(timestamp)"; printf '%s\n' "current memory cleared" ;;
    attach) [ -n "$rest" ] && { ATTACH_FILES+=("$rest"); printf 'queued attachment: %s\n' "$rest"; } || printf '%s\n' "usage: :attach FILE" ;;
    attachments) list_attachments_runtime ;;
    editor)
      editor_buffer="$(compose_with_editor_basic)"
      [ -n "$editor_buffer" ] && send_one_message "$editor_buffer" || printf '%s\n' "editor returned empty prompt"
      ;;
    *) printf 'unknown runtime command: :%s\n' "$command"; printf '%s\n' "type :help for available commands" ;;
  esac
  return 0
}

# ==========================================================
# ARGUMENTS
# ==========================================================

POSITIONAL_ARGS=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help|help) ACTION=help;;
    --version) ACTION=version;;
    --demo) DEMO_MODE=1;;
    -i|--interactive) INTERACTIVE=1;;
    -n|--new) FORCE_NEW=1;;
    -p|--prompt) shift; PROMPT_TEXT="${1:-}";;
    --input) shift; INPUT_FILE="${1:-}";;
    -o|--output) shift; OUTPUT_PATH="${1:-}";;
    -s|--session) shift; SESSION_NAME="${1:-}";;
    --stream) STREAM=1;; --no-stream) STREAM=0;;
    --web) WEB_SEARCH=1;; --no-web) WEB_SEARCH=0;;
    --web-engine) shift; [ "$#" -gt 0 ] || { printf '%s\n' 'ERROR: --web-engine requires ENGINE' >&2; exit 2; }; WEB_SEARCH_ENGINE="$1"; case "$1" in auto|native|exa|parallel|perplexity|firecrawl) ;; *) printf '%s\n' 'ERROR: unsupported web engine' >&2; exit 2 ;; esac ;;
    --web-max-results) shift; [ "$#" -gt 0 ] || { printf '%s\n' 'ERROR: --web-max-results requires N' >&2; exit 2; }; case "$1" in ''|*[!0-9]*) printf '%s\n' 'ERROR: results must be 0..25' >&2; exit 2 ;; esac; [ "${#1}" -le 2 ] && [ "$((10#$1))" -ge 0 ] && [ "$((10#$1))" -le 25 ] || { printf '%s\n' 'ERROR: results must be 0..25' >&2; exit 2; }; WEB_SEARCH_MAX_RESULTS="$1" ;;
    --web-max-uses) shift; [ "$#" -gt 0 ] || { printf '%s\n' 'ERROR: uses must be 0..25' >&2; exit 2; }; case "$1" in ''|*[!0-9]*) printf '%s\n' 'ERROR: uses must be 0..25' >&2; exit 2 ;; esac; [ "${#1}" -le 2 ] && [ "$((10#$1))" -ge 0 ] && [ "$((10#$1))" -le 25 ] || { printf '%s\n' 'ERROR: uses must be 0..25' >&2; exit 2; }; WEB_SEARCH_MAX_USES="$1" ;;
    --ai-download) shift; AI_FILE_DOWNLOAD_MODE="${1:-ask}";;
    --status|--progress) STATUS_UPDATES=1;; --no-status|--no-progress) STATUS_UPDATES=0;;
    --voice-input|--transcribe) shift; VOICE_INPUT_PATH="${1:-}";;
    --speak) VOICE_OUTPUT=1;; --no-speak) VOICE_OUTPUT=0;;
    --turns) ACTION=turns; shift; TURN_SESSION="${1:-}";;
    --branch) ACTION=branch; shift; TURN_SESSION="${1:-}"; shift; TURN_NUMBER="${1:-}";;
    --edit-turn) ACTION=edit-turn; shift; TURN_SESSION="${1:-}"; shift; TURN_NUMBER="${1:-}";;
    --export) ACTION=export; shift; EXPORT_SESSION="${1:-}";;
    --search) ACTION=search; shift; SEARCH_QUERY="${1:-}";;
    --all-sessions) SEARCH_ALL=1;;
    --format) shift; EXPORT_FORMAT="${1:-markdown}";;
    --rename-session) ACTION=rename; shift; RENAME_OLD="${1:-}"; shift; RENAME_NEW="${1:-}";;
    --loopback) LOOPBACK=1;;
    --loopback-file) shift; LOOPBACK_FILE="${1:-}"; [ -n "$LOOPBACK_FILE" ] || { printf 'ERROR: --loopback-file requires a path.\n' >&2; exit 2; };;
    --doctor) ACTION=doctor;; --credits) ACTION=credits;; --list) ACTION=list;; --models) ACTION=models;; --routers) ACTION=models; MODEL_TYPE_FILTER="routers";;
    --favorites) ACTION=favorites;; --favorite-model) ACTION=favorite; shift; TEST_MODEL="${1:-}";; --unfavorite-model|--unbookmark-model) ACTION=unfavorite; shift; TEST_MODEL="${1:-}";;
    --model-type) shift; MODEL_TYPE_FILTER="${1:-all}";; --sort) shift; MODEL_SORT_ORDER="${1:-$MODEL_SORT_ORDER}";; --min-input-tokens) shift; MODEL_MIN_INPUT_TOKENS="${1:-$MODEL_MIN_INPUT_TOKENS}";; --min-output-tokens) shift; MODEL_MIN_OUTPUT_TOKENS="${1:-$MODEL_MIN_OUTPUT_TOKENS}";;
    --require-tokens) MODEL_FILTER_REQUIRE_TOKENS=1;; --allow-missing-tokens) MODEL_FILTER_REQUIRE_TOKENS=0;; --theme) shift; THEME="${1:-default}";; --send-input) shift; SEND_INPUT="${1:-ctrl_d}";; --code-attachment-min-lines) shift; CODE_ATTACHMENT_MIN_LINES="${1:-5}";;
    --config) ACTION=config;; --set) ACTION=set-config; shift; SET_KEY="${1:-}"; shift; SET_VALUE="${1:-}";; --unset) ACTION=unset-config; shift; UNSET_KEY="${1:-}";;
    --update-models) ACTION=update-models;; --select-model) ACTION=select;; --test-model) ACTION=test; shift; TEST_MODEL="${1:-}";; --set-api-key) ACTION=set-key;; --forget-api-key) ACTION=forget-key;; --save) SAVE=1;;
    --attach) shift; ATTACH_FILES+=("${1:-}");; --resume) RESUME=1; shift; SESSION_NAME="${1:-}";; --) shift; while [ "$#" -gt 0 ]; do POSITIONAL_ARGS+=("$1"); shift; done; break;;
    -*) printf 'unknown option: %s\n' "$1" >&2; exit 2;;
    *) POSITIONAL_ARGS+=("$1");;
  esac; shift
done

if [ "${#POSITIONAL_ARGS[@]}" -eq 1 ] && [ -z "$SESSION_NAME" ] && [ -z "$PROMPT_TEXT" ] && [ -z "$INPUT_FILE" ]; then
  if [ -f "${POSITIONAL_ARGS[0]}" ]; then INPUT_FILE="${POSITIONAL_ARGS[0]}"; else SESSION_NAME="${POSITIONAL_ARGS[0]}"; fi
elif [ "${#POSITIONAL_ARGS[@]}" -eq 2 ] && [ -z "$INPUT_FILE" ] && [ -z "$OUTPUT_PATH" ]; then
  INPUT_FILE="${POSITIONAL_ARGS[0]}"; OUTPUT_PATH="${POSITIONAL_ARGS[1]}"
elif [ "${#POSITIONAL_ARGS[@]}" -gt 0 ]; then
  printf 'unexpected argument: %s\n' "${POSITIONAL_ARGS[*]}" >&2; exit 2
fi

# The action dispatcher below deliberately stays near the bottom of the file.
# By the time we reach it, config has been loaded and helper functions exist.
# This keeps command behavior easy to audit: parse flags, dispatch one action,
# or fall through into the interactive chat loop.
case "$ACTION" in
  turns) turns_command "$TURN_SESSION"; exit $?;;
  branch) branch_session_command "$TURN_SESSION" "$TURN_NUMBER"; exit $?;;
  edit-turn) edit_turn_command "$TURN_SESSION" "$TURN_NUMBER"; exit $?;;
  export) export_session_command "$EXPORT_SESSION" "$EXPORT_FORMAT" "$OUTPUT_PATH"; exit $?;;
  search) if [ -z "$SESSION_NAME" ]; then SEARCH_ALL=1; fi; search_command "$SEARCH_QUERY" "$SEARCH_ALL" "$( [ -n "$SESSION_NAME" ] && resolve_session_arg "$SESSION_NAME" || true )"; exit $?;;
  rename) rename_session_command "$RENAME_OLD" "$RENAME_NEW"; exit $?;;
  help) help_menu; exit 0;; version) printf '%s version %s\n' "$PROGRAM" "$VERSION"; exit 0;; doctor) doctor_command; exit 0;; credits) credits; exit 0;; list) list_sessions; exit 0;; models) list_models; exit $?;; favorites) list_favorite_models; exit $?;; favorite) favorite_model_command "$TEST_MODEL"; exit $?;; unfavorite) unfavorite_model_command "$TEST_MODEL"; exit $?;; config) print_config_summary; exit 0;; set-config) [ -n "$SET_KEY" ] || { printf 'ERROR: --set requires KEY VALUE\n' >&2; exit 2; }; save_config_value "$SET_KEY" "$SET_VALUE"; exit $?;; unset-config) [ -n "$UNSET_KEY" ] || { printf 'ERROR: --unset requires KEY\n' >&2; exit 2; }; remove_config_key "$UNSET_KEY"; exit $?;; update-models) update_models_cache; exit $?;; select) select_model; exit $?;; test) [ -n "$TEST_MODEL" ] || { printf 'ERROR: --test-model requires a model\n' >&2; exit 2; }; test_model "$TEST_MODEL"; test_status=$?; if [ "$test_status" -eq 0 ] && [ "$SAVE" -eq 1 ]; then save_config_value MODEL "$TEST_MODEL" || test_status=$?; fi; exit "$test_status";; set-key) set_api_key_command; exit $?;; forget-key) forget_api_key_command; exit $?;;
esac

# ==========================================================
# CHAT LOOP
# ==========================================================

require_api_key || exit 1
if [ "$FORCE_NEW" -eq 1 ] && { [ -n "$SESSION_NAME" ] || [ -n "$OUTPUT_PATH" ] || [ "$RESUME" -eq 1 ]; }; then
  printf '%s
' "ERROR: --new cannot be combined with --session, --output, --resume, or a session operand" >&2
  exit 2
fi
SESSION_ARG="$SESSION_NAME"
if [ -n "$OUTPUT_PATH" ]; then
  SESSION_FIXED_PATH=1
  SESSION_NAME="$(basename "$OUTPUT_PATH" .log)"
  SESSION="$OUTPUT_PATH"
elif [ -z "$SESSION_ARG" ]; then
  SESSION_NAME="$(default_session_name_unique)" || exit 1
  SESSION="$(resolve_session_path "$SESSION_NAME")"
else
  SESSION="$(resolve_session_arg "$SESSION_ARG")" || exit 1
  SESSION_NAME="$(basename "$SESSION" .log)"
fi
SESSION_WAS_EMPTY=1
[ -s "$SESSION" ] && SESSION_WAS_EMPTY=0
[ "$RESUME" -eq 1 ] && [ ! -f "$SESSION" ] && { printf 'session not found: %s
' "$SESSION_NAME" >&2; exit 1; }
ensure_prechat_line "$SESSION" || exit 1
SESSION="$(absolute_session_path "$SESSION")" || exit 1
SESSION_ID="$(ensure_session_id "$SESSION")" || exit 1
# A supplied descriptive name is already a naming decision; preserve it.
if [ "$SESSION_WAS_EMPTY" -eq 1 ] && [ -n "$SESSION_ARG" ] && ! session_needs_name; then
  write_session_title_metadata "$SESSION" "$(basename "$SESSION_ARG" .log)" || exit 1
fi
case "$SESSION" in "$SESSION_DIR"/*) ;; *) SESSION_FIXED_PATH=1 ;; esac
normalize_web_options
maybe_prompt_session_name
load_context || exit 1
if truthy "$DEMO_MODE" && [ -z "$MODEL" ]; then MODEL="demo/auto"; fi
ACTIVE_MODEL="$(effective_model)"
NOTICE_VISIBLE=0
truthy "$STARTUP_NOTICE" && [ "$SESSION_WAS_EMPTY" -eq 1 ] && NOTICE_VISIBLE=1

# ----------------------------------------------------------
# MAINTAINER COMMENTARY: loopback output routing
# Loopback is for terminal users who want to pipe AI responses to another
# program while still seeing the normal ttychatter interface. A normal chat
# client prints prompts, separators, and status text; that output is useful to
# humans but contaminates pipelines. When --loopback is active, fd 3 preserves
# the original stdout for clean AI text, while ordinary interface output is
# redirected to stderr.
# ----------------------------------------------------------
exec 3>&1
if [ "$LOOPBACK" -eq 1 ]; then
  exec 1>&2
fi

show_session_name
web_status
printf '%s (bash-only)\n' "$PROGRAM"; printf 'session: %s\n' "$SESSION"; printf 'model: %s\n' "$(model_label)"; printf 'Ctrl+D to send, :help for commands, Ctrl+C to exit\n\n'
if [ "$NOTICE_VISIBLE" -eq 1 ]; then printf '%s\n\n' "$TC_PRECHAT_LINE"; fi
send_one_message() {
  local buffer="$1" ts context output cleaned output_status save_failed=0
  ts="$(timestamp)"
  [ -z "$buffer" ] && { printf '%s
' "empty message ignored"; return 1; }
  printf '[%s] %s: %s\n' "$ts" "$USER_NAME" "$buffer" >> "$SESSION" || { printf '%s\n' 'Cannot save prompt; no request was sent.' >&2; return 1; }
  add_context "User: $buffer"
  context="$(build_context)"
  status_update "preparing request for $ACTIVE_MODEL"
  output="$(call_model "$ACTIVE_MODEL" "$context")"; output_status=$?
  if [ "$output_status" -ne 0 ]; then
    printf '[%s] system: response-error: %s\n' "$(timestamp)" "${output:-OpenRouter request failed}" >> "$SESSION"
    [ -z "$output" ] || printf '%s\n' "$output" >&2
    emit_session_path
    return "$output_status"
  fi
  ATTACH_FILES=()
  printf '[%s] AI: %s\n' "$(timestamp)" "$output" >> "$SESSION" || { save_failed=1; printf '%s\n' 'Response received, but the log write failed. Copy the displayed reply before closing.' >&2; }
  cleaned="$(printf '%s\n' "$output" | extract_code_blocks)" || { cleaned="$output"; save_failed=1; printf '%s\n' 'Attachment extraction failed; showing the complete original reply.' >&2; }
  add_context "Assistant: $cleaned"
  write_context_snapshot "$(timestamp)" || { save_failed=1; printf '%s\n' 'The response is displayed, but its memory snapshot could not be saved.' >&2; }
  if [ "$LOOPBACK" -eq 1 ]; then
    printf '%s\n' "$cleaned" >&3
  fi
  if [ -n "$LOOPBACK_FILE" ]; then
    printf '%s
' "$cleaned" >> "$LOOPBACK_FILE"
  fi
  LAST_AI_OUTPUT="$cleaned"
  if [ "$save_failed" -eq 0 ]; then generate_session_title "$buffer" "$cleaned"; fi
  if truthy "$VOICE_OUTPUT" || [ "$SPEAK_OVERRIDE" = "1" ]; then run_voice_output_command "$cleaned" || true; fi
  if [ "$LOOPBACK" -eq 1 ]; then printf '%s
' "$cleaned"; else print_ai_output "$cleaned"; fi
  emit_session_path
  return "$save_failed"
}

# ----------------------------------------------------------
# MAINTAINER COMMENTARY: function read_user_input
# SEND_INPUT parity for the bash-only edition.  SEND_INPUT=enter sends one
# physical input line each time the user presses Enter.  The default
# SEND_INPUT=ctrl_d keeps the traditional multiline shell behavior where cat
# reads until EOF.  Ctrl+G cannot be implemented portably in ordinary cooked
# shell input, so it is accepted as a config idea but treated like ctrl_d here.
# ----------------------------------------------------------
read_user_input() {
  if [ "$SEND_INPUT" = "enter" ]; then
    IFS= read -r buffer
    return $?
  fi
  buffer="$(cat)"
  return 0
}

if [ -n "$VOICE_INPUT_PATH" ]; then
  PROMPT_TEXT="$(run_voice_input_command "$VOICE_INPUT_PATH")" || exit $?
fi
if [ -n "$INPUT_FILE" ]; then
  if [ "$INPUT_FILE" = "-" ]; then PROMPT_TEXT="$(cat)"; else PROMPT_TEXT="$(cat "$INPUT_FILE")" || exit 1; fi
fi
if [ -n "$PROMPT_TEXT" ] || { [ ! -t 0 ] && [ "$INTERACTIVE" -eq 0 ]; }; then
  if [ -z "$PROMPT_TEXT" ]; then PROMPT_TEXT="$(cat)"; fi
  send_one_message "$PROMPT_TEXT"
  exit $?
fi

while true; do
  ts="$(timestamp)"; printf '[%s] %s> ' "$ts" "$USER_NAME"; if ! read_user_input; then [ -t 0 ] || exit 0; continue; fi; if [ -z "$buffer" ]; then [ -t 0 ] || exit 0; continue; fi
  RUNTIME_SEND_OVERRIDE=""
  if handle_runtime_command "$buffer"; then
    continue
  else
    runtime_status=$?
    if [ "$runtime_status" -eq 2 ]; then buffer="$RUNTIME_SEND_OVERRIDE"; fi
  fi
  if [ "$NOTICE_VISIBLE" -eq 1 ]; then clear_screen; NOTICE_VISIBLE=0; fi
  send_one_message "$buffer"
done
