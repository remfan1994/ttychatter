# ttychatter

`ttychatter` is a terminal-first OpenRouter chat client family. The project keeps a plain text log as the durable session record, reconstructs context from that log, and offers several front ends that share the same config, model cache, session directory, attachments directory, and session metadata.

The current implementations are:

- `ttychatter` - C command-line client, the most complete implementation.
- `ttychatter.python` - Bash plus Python 3 helper client, close to C feature parity for line-oriented use.
- `ttychatter.bash` - Bash-only client for constrained systems; intentionally scaled down where JSON or edit/branch behavior would be fragile.
- `ttychatter.ncurses` - Python ncurses TUI for interactive terminal use.
- `ttychatter.mailbot` - Email interface that delegates chat to the C, Bash, or Bash+Python client.

The October 18, 2026 maintenance release adds terminal-native ncurses rendering,
web search in every interface, named log files with stable session IDs, and
repairs to streaming, memory replay, file preservation, and mail delivery.
See `MAINTENANCE_REPORT.txt` for the change list, verification, and future work.

The design equation is:

```text
session = named log file + stable ID + metadata + user/assistant turns
memory  = reconstructed context from saved logs
ui      = one of C, Bash, Bash+Python, or ncurses
email   = mailbot + one of the line-oriented clients
```

The storage layer is intentionally boring. The user-facing layer is allowed to be friendly.

## Features

Features available across the family, with interface differences listed below:

- Interactive chat sessions.
- Batch prompt from a file, stdin, or `--prompt TEXT`.
- Human session titles reflected in log filenames and displayed by `--list`.
- Stable session IDs that survive renaming, with old-name aliases for resuming.
- Prominent naming prompts for interactive unnamed sessions.
- Safe physical renaming with `--rename-session SESSION TITLE` and `:rename TITLE` where available.
- Optional AI-generated titles, off by default.
- Opt-in OpenRouter web search, with returned source links and response metadata.
- OpenRouter model cache, model listing, model filtering, favorites, selection, and model testing.
- User-to-AI attachments where supported by the implementation and model.
- Conservative receipt of provider-returned file attachments with ask/metadata/auto/off modes.
- Session search.
- Branching from an earlier turn.
- Export to text, Markdown, or JSON where supported.
- Streaming output where supported.
- Local status/progress messages that report client work, not hidden model thoughts.
- External voice input and voice output hooks.
- XDG-compatible config/cache/data paths.

The C client is the reference implementation. The Python-helper client follows it most closely. Bash-only and ncurses scale features down according to their interface constraints.

## Install

### C client

```sh
make
make install PREFIX="$HOME/.local"
```

Run these commands from the archive's `ttychatter` directory. `make` builds
`c/ttychatter`; `make install` installs the C binary, all four scripts, and their
manuals. Ensure `$HOME/.local/bin` is on PATH. Required C build dependencies are
a C compiler, `make`, `pkg-config`, and development headers/libraries for
`libcurl` and `json-c`. See `c/README` for distribution-specific installation.

To build or install only the C edition, use `make -C c` and
`make -C c install PREFIX="$HOME/.local"`.

### Script clients

```sh
make install-scripts PREFIX="$HOME/.local"
```

`ttychatter.bash` requires Bash, curl, and common Unix tools. Install `jq` for
web search and complete JSON response/memory handling; the reduced mode fails
explicitly when a feature needs it. `ttychatter.python` requires Bash, curl, and
Python 3. `ttychatter.ncurses` requires Python 3 with the curses module.
`ttychatter.mailbot` requires Python 3 and one of the three line-oriented
backends. Its setup is documented in `mailbot/ttychatter.mailbot.walkthrough.txt`.

## Configuration

The shared config file is:

```text
$XDG_CONFIG_HOME/ttychatter/openrouter/config
```

If `XDG_CONFIG_HOME` is unset, this becomes:

```text
~/.config/ttychatter/openrouter/config
```

The file is line-oriented:

```text
KEY=VALUE
```

Common keys:

```text
MODEL=openrouter/auto
CONTEXT_TURNS=12
SESSION_DIR=~/.local/share/ttychatter/openrouter/sessions
ATTACHMENT_DIR=~/.local/share/ttychatter/openrouter/attachments
MODEL_CACHE_FILE=~/.cache/ttychatter/openrouter/models.json
MODEL_FAVORITES_FILE=~/.config/ttychatter/openrouter/model-favorites
STARTUP_NOTICE=1
STREAM=0
STATUS_UPDATES=0
SESSION_AUTO_TITLE=0
PROMPT_SESSION_NAME=1
SESSION_TITLE_MODEL=openrouter/auto
SESSION_TITLE_MAX_WORDS=8
WEB_SEARCH=0
WEB_SEARCH_ENGINE=auto
WEB_SEARCH_MAX_RESULTS=5
WEB_SEARCH_MAX_USES=3
VOICE_INPUT_CMD=
VOICE_OUTPUT_CMD=
VOICE_OUTPUT=0
SEND_INPUT=ctrl_d
THEME=terminal
INPUT_HEIGHT=3
CODE_ATTACHMENT_MIN_LINES=5
MAX_ATTACHMENT_BYTES=1048576
AI_FILE_DOWNLOAD_MODE=ask
```

`MODEL` is the normal conversation model. `SESSION_TITLE_MODEL` is only used
when `SESSION_AUTO_TITLE=1`; it is a secondary model for generating a short
session title. Automatic title generation is off by default and does not enable
web search. Manual titles are not restricted by `SESSION_TITLE_MAX_WORDS`.

`THEME` is a compatibility key in ncurses: all old theme values now use the
terminal's foreground and background. `INPUT_HEIGHT` controls the visible
ncurses draft height from 1 to 12 rows, subject to available terminal space.

API keys can come from:

```sh
export OPENROUTER_API_KEY=...
```

or from config:

```text
OPENROUTER_API_KEY=...
```

or from the encrypted GPG key file where supported:

```text
$XDG_CONFIG_HOME/ttychatter/openrouter/api-key.gpg
```

The programs also read the older `~/.config/ttychatter/config` and `~/.config/ttychatter/api-key.gpg` locations as compatibility fallbacks when the provider-specific files are absent.

## XDG paths

Default shared paths:

```text
config:      $XDG_CONFIG_HOME/ttychatter/openrouter/config
api key gpg: $XDG_CONFIG_HOME/ttychatter/openrouter/api-key.gpg
favorites:   $XDG_CONFIG_HOME/ttychatter/openrouter/model-favorites
models:      $XDG_CACHE_HOME/ttychatter/openrouter/models.json
sessions:    $XDG_DATA_HOME/ttychatter/openrouter/sessions/
attachments: $XDG_DATA_HOME/ttychatter/openrouter/attachments/
```

Fallbacks when XDG variables are unset:

```text
config:      ~/.config/ttychatter/openrouter/config
api key gpg: ~/.config/ttychatter/openrouter/api-key.gpg
favorites:   ~/.config/ttychatter/openrouter/model-favorites
models:      ~/.cache/ttychatter/openrouter/models.json
sessions:    ~/.local/share/ttychatter/openrouter/sessions/
attachments: ~/.local/share/ttychatter/openrouter/attachments/
```

The four chat clients share the session format. A session created by one
appears in `--list` in the others. Mailbot uses its selected backend and
isolates configured per-user homes and session directories.

## Basic use

Start a new interactive session:

```sh
ttychatter
```

Explicit interactive mode:

```sh
ttychatter -i
ttychatter --interactive
```

Send a text file once and auto-save the session:

```sh
ttychatter input.txt
```

Send a text file once and choose the output log:

```sh
ttychatter input.txt output.log
```

Send direct prompt text:

```sh
ttychatter --prompt "Explain this error" 
```

Read stdin as one batch prompt:

```sh
echo "Summarize this" | ttychatter
```

Use a named session:

```sh
ttychatter --session notes --prompt "Continue from these notes"
ttychatter -i notes
```

Force a new timestamped session:

```sh
ttychatter --new
```

## Sessions and titles

Interactive unnamed sessions ask for a name by default. Ncurses also marks an
unnamed session in the header and asks again after its first successful reply
if it still has no title. The Esc command menu contains the naming action. Batch
and piped input are never consumed by a naming prompt. Set
`PROMPT_SESSION_NAME=0` to disable terminal naming prompts.

A named log contains a full human title and an opaque identity:

```text
[12:34:56] system: session-id: 0123456789abcdef0123456789abcdef
[12:34:56] system: session-title: Research Notes
```

Giving or changing a title also safely renames the physical file. For example,
the ncurses edition produces:

```text
Research_Notes--0123456789ab.log
```

The exact space replacement can differ by client. All use a sanitized title
and the same first 12 characters of the stable ID; a numeric suffix resolves
filename collisions without overwriting another log. The ID remains unchanged
through later renames. The previous filename stem is retained as an alias.
Lookups by an exact human title or old alias succeed when unambiguous; choose
the exact filename if multiple sessions have the same title.

An explicit `--session notes` starts as `notes.log` with title `notes` so the
same command can reopen it. Explicit output paths are respected. Unnamed
sessions still use `session-YYYY-MM-DD-HH-MM-SS.log` until named. Existing logs
remain usable; this update does not guess titles or bulk-rename old logs.

```sh
ttychatter --list
ttychatter --rename-session session-2026-06-04-12-00-00 "Research Notes"
ttychatter --resume "Research Notes"
```

The rename command prints the resulting physical path. In the line clients,
use `:rename Research Notes`. In ncurses use the Esc command menu or the F4 session browser.
Previously extracted attachments retain their paths so saved references keep
working.

Mailbot uses ordinary email subjects as human titles. Its rename operation
tracks the new file and preserves the original subject's session association.
Replies to `tc:{new}` carry the created session's actual selector. Unnamed
mail conversations include a prominent reminder and a rename command.

Memory snapshots are recorded as `CONTEXT_SNAPSHOT_JSON` system entries.
Readers restore the latest complete snapshot and then append newer real turns;
an empty snapshot means deliberately cleared memory. Legacy `CONTEXT_BEGIN` /
`CONTEXT_END` snapshots remain readable. Metadata, citations metadata, and the
project notice are not replayed as user prompts. The Bash-only client requires
`jq` to resume a log containing JSON snapshots safely.

New logs begin with the project pre-chat line:

```text
Everyone is encouraged to get the cruelty-free vegetarian alternatives and remember the bloodguilt curse from the Bible... http://bloodguiltcurse.net
```

That line is visible to the user and stored in the log, but session context rebuilding skips it so it is not replayed as conversation memory.

## Web search

All five interfaces now offer web search. For a line-oriented client:

```sh
ttychatter --web --prompt "Find recent documentation for this issue and cite sources"
ttychatter.python --web --web-engine exa --web-max-results 5 --prompt "Research this topic"
ttychatter.bash --no-web --prompt "Continue without adding the web tool"
```

The common CLI options are `--web`, `--no-web`, `--web-engine ENGINE`,
`--web-max-results N`, and `--web-max-uses N`. Interactive line clients also
provide `:web on`, `:web off`, and `:web status`. In ncurses, web search is configured with the other settings under F5 Options. Mailbot accepts `tc:web on`, `tc:web off`, and
`tc:web status`; its operator CLI accepts the same web flags.

The default is off. Supported engines are `auto`, `native`, `exa`, `parallel`,
`perplexity`, and `firecrawl`. Defaults request five results per search and cap server-tool search rounds at three. A value of 0 omits ttychatter's limit: for results it leaves the provider default, and for search rounds it omits `max_tool_calls`. Result settings accept 0..25; Perplexity is capped at 20 when an explicit result limit is used. These settings are not a dollar budget.

The clients send OpenRouter's `openrouter:web_search` server tool. OpenRouter
executes it; no local search process or separate engine key is needed. Enabling
the tool lets the model decide whether to search and may incur search charges
in addition to model tokens. It does not guarantee that every answer searches.
Existing search-specific models or externally configured provider presets can
have their own behavior; `--no-web` controls the tool added by ttychatter.

Returned citation annotations become a visible Sources section when supplied
by the provider. Response IDs, usage, provider/model information, annotations,
and supplied reasoning metadata are retained in separate system records.
The ncurses client uses the agentic server tool when possible. In Web compatibility `auto`, a model whose cached metadata lacks `tools`, or a runtime 404 saying no tool-capable endpoint exists, falls back once to OpenRouter's still-supported web plugin. The plugin performs one pre-answer search instead of an agentic multi-search loop. `server-tool` and `plugin` modes can also be selected explicitly.

Current OpenRouter references checked for this release:
https://openrouter.ai/tool/web-search/
https://openrouter.ai/blog/agentic-web-tools/

## Ncurses layout and controls

Ncurses inherits the terminal's foreground and background. It uses bold and
reverse video for emphasis and selection, without defining its own palette.
The old action bars are removed. The draft starts at three rows and can be
hidden while reading; hiding preserves both text and cursor position.

| Key | Action |
| --- | --- |
| Esc | Full scrollable command menu; F1 remains an optional alias |
| F11 | Hide or show the draft |
| PgUp / PgDn | Scroll the transcript by a visible page |
| Up / Down or k / j in read mode | Scroll transcript one line |
| u / d in read mode | Scroll transcript half a page |
| g / G in read mode | Go to oldest / latest transcript position |
| Arrow keys with draft visible | Move the insertion cursor |
| F2 / F3 / F4 | Models / API key / sessions |
| F5 / F6 / F7 | Options / memory / files and attachments |
| F8 | External-editor options; Enter on Open in external editor launches it |
| F9 | Credits |
| F10 | Confirm closing; only Y exits |
| Configured send key | Enter by default; Ctrl+G is reserved only when SEND_INPUT=ctrl_g |

F7, F8, and F9 can switch directly between their screens, so a mistaken
function key does not force entry into an editor. Attachment actions intentionally have no global Ctrl+K/Ctrl+L-style shortcuts. The editor menu also offers
configuration and Save last reply to file. F10 asks:

```text
Session will be ended and unsaved data lost. Really close? Y/N
```

Generation in ncurses runs on a background request worker. The main curses loop continues to accept keys while OpenRouter is generating, so Options, Files, model/session screens, transcript scrolling, and other function-key screens remain usable. The first rendered response text automatically switches the chat view into read mode. Settings changed while a request is active apply to the next request, not the in-flight request.

The F4 session browser sorts newest sessions first by default. `s` changes sorting, `/` searches session titles/filenames, `f` performs a case-insensitive full-text search inside session logs, and `x` clears search filters. Session switching/renaming/deletion is temporarily blocked only while a response is in flight so a completed reply cannot be written to the wrong session.

The input has a visible insertion-cell marker and hardware cursor, plus a
line/column indicator in the footer. Wrapping and cursor placement account for
tabs, combining marks, and wide characters already in the draft. Direct key
entry remains ASCII-oriented; use the external editor for broader Unicode
input. Drafts and pending attachments are retained per session while this
process remains open; they are not yet saved for crash recovery.

The transcript caches wrapped lines to reduce work during redraws and streaming. Both streaming and nonstreaming provider requests run outside the curses event loop. Closing the session requests cancellation of an active stream; otherwise navigation and configuration remain available while generation continues.

## Models

Refresh the model cache:

```sh
ttychatter --update-models
```

List cached models:

```sh
ttychatter --models
```

Useful filters:

```sh
ttychatter --models --model-type free
ttychatter --models --min-input-tokens 100000
ttychatter --models --min-output-tokens 16000
ttychatter --models --show-preview
ttychatter --models --hide-preview
ttychatter --models --favorites-only
```

Select or test models:

```sh
ttychatter --select-model --save
ttychatter --test-model openrouter/auto
ttychatter --test-model openrouter/auto --save
```

Favorites:

```sh
ttychatter --favorite-model openrouter/auto
ttychatter --favorites
ttychatter --unfavorite-model openrouter/auto
```

## Attachments

Attach files to a prompt where the implementation and selected model support it:

```sh
ttychatter --attach notes.txt --prompt "Use this file"
ttychatter --attach image.png --prompt "Describe this image"
```

Interactive command where available:

```text
:attach notes.txt
:attachments
:clear-attachments
```

Large response code blocks can be extracted into the shared attachment directory. The threshold is controlled by:

```text
CODE_ATTACHMENT_MIN_LINES=5
```

Provider-returned files are controlled separately from user-to-AI attachments.
When an AI/provider response includes file metadata, data URLs, base64 payloads,
or documented file-like response parts, ttychatter does not silently save them
by default.

```text
AI_FILE_DOWNLOAD_MODE=ask       # prompt before saving when interactive
AI_FILE_DOWNLOAD_MODE=metadata  # list returned files without saving
AI_FILE_DOWNLOAD_MODE=auto      # save all returned files
AI_FILE_DOWNLOAD_MODE=off       # ignore returned file metadata
```

CLI and interactive forms where supported:

```sh
ttychatter --ai-download metadata --prompt "Generate files if supported"
```

```text
:ai-download ask
:ai-download auto
```

Saved provider-returned files go under:

```text
$ATTACHMENT_DIR/received/
```

In noninteractive use, ask mode records metadata only because there is no user
to answer the prompt. The mailbot follows the same conservative rule and can
attach saved returned files to outgoing email only when download mode is set to
auto. Use `--no-stream` for provider-returned file receipt in the C client;
its streaming path handles text, citations, and metadata. Ncurses streaming
also treats ask mode as metadata-only; select auto
explicitly to save returned files during a stream. Saved file and extracted
code names are reserved without overwriting an earlier reply's files.

## Search, turns, branching, and export

Search sessions:

```sh
ttychatter --search "kernel panic" --all-sessions
```

Show numbered turns:

```sh
ttychatter --turns notes
```

Branch after a turn:

```sh
ttychatter --branch notes 4
```

Edit a previous user turn and branch from there where supported:

```sh
ttychatter --edit-turn notes 3
```

Export:

```sh
ttychatter --export notes --format markdown --output notes.md
ttychatter --export notes --format text
ttychatter --export notes --format json
```

The Bash-only edition does not claim full edit-turn or JSON export support. Use the C or Python-helper client for those.

## Streaming and status

Streaming shows assistant output as it arrives. Press ESC during an active stream to stop it; ttychatter saves the partial assistant reply with an interruption marker and leaves the session open:

```sh
ttychatter --stream --prompt "Write a short example"
```

Disable streaming:

```sh
ttychatter --no-stream
```

Stream parsing continues through the final usage packet to `[DONE]` instead of
stopping at the first finish reason. HTTP errors and errors carried inside a
successful HTTP response are reported explicitly. Interrupted or failed
streams preserve available partial text. The clients do not automatically
retry a paid generation. Closing a stream requests cancellation, but provider
processing and billing after cancellation depend on the provider.

Status/progress updates report local client work:

```sh
ttychatter --status --prompt "Summarize this file"
```

Config equivalents:

```text
STREAM=1
STATUS_UPDATES=1
```

Interactive commands where available:

```text
:stream on
:stream off
:status on
:status off
:progress on
:progress off
```

Status messages are not hidden chain-of-thought. They are local progress reports such as loading context, preparing a request, waiting for the provider, and saving the log.

## Voice hooks

Voice features are external command hooks. ttychatter does not embed a microphone or speaker stack.

Example transcription hook:

```text
VOICE_INPUT_CMD=whisper-cli -m ~/models/ggml-base.en.bin -f %f -otxt -of -
```

Example TTS hook.  Without `%f`, ttychatter pipes the assistant reply to the command's standard input.  With `%f`, ttychatter writes the reply to a temporary text file and substitutes that path:

```text
VOICE_OUTPUT_CMD=your-tts-command
```

Command line:

```sh
ttychatter --voice-input speech.wav
ttychatter --transcribe speech.wav    # alias for --voice-input, not a transcript-only mode
ttychatter --speak --prompt "Read this response aloud"
```

Interactive commands where available:

```text
:voice speech.wav
:transcribe speech.wav
:speak on
:speak off
:speak last
```

## Runtime colon commands

Interactive line-oriented clients support local commands beginning with `:`. Common commands include:

```text
:help
:rename TITLE
:list
:turns
:branch N
:fork N
:edit N
:edit-turn N
:models
:update-models
:select-model
:test-model MODEL
:favorite MODEL
:unfavorite MODEL
:attach FILE
:memory
:search TEXT
:search-all TEXT
:stream on|off
:web on|off|status
:status on|off
:progress on|off
:voice FILE
:transcribe FILE
:speak on|off|last
:config
:set KEY VALUE
:unset KEY
:credits
:quit
```

The exact set varies by implementation. Use `:help` inside the running client.

## Implementation parity

Expected parity model:

```text
C                 reference implementation; full CLI feature target
Bash+Python       close line-oriented parity with C
Bash-only         dependency-light; scaled down for shell limitations
ncurses           interactive TUI; batch-only features are intentionally limited
mailbot           email interface using a line-oriented backend
```

High-level matrix:

```text
feature                   C       python   bash     ncurses
interactive chat          yes     yes      yes      yes
batch prompt              yes     yes      yes      no
XDG shared paths          yes     yes      yes      yes
human titles/log rename   yes     yes      yes      yes
stable IDs/old aliases    yes     yes      yes      yes
naming prompts            yes     yes      yes      yes
AI-generated titles       yes     yes      partial  yes
web search                yes     yes      jq       yes
JSON memory snapshots     yes     yes      jq       yes
models/cache/favorites    yes     yes      partial  yes
user attachments          yes     yes      partial  yes
AI returned files         yes     yes      partial  yes
session search            yes     yes      simple   limited
turn listing              yes     yes      yes      yes
branch from turn          yes     yes      yes      yes
edit-turn branch          yes     yes      no       no
export text/markdown      yes     yes      yes      yes
export JSON               yes     yes      no       yes
streaming                 yes     yes      jq       yes
status/progress           yes     yes      yes      footer
voice hooks               yes     yes      plumbing external
mouse support             no      no       no       yes
```

`partial` means the feature exists in a reduced form or depends on optional
tools. `jq` means that optional parser is required for the feature. Mailbot
inherits chat, web search, model, and file behavior from its backend. It sends
completed email replies, not a live terminal stream.

## Diagnostics

Run local diagnostics:

```sh
ttychatter --doctor
```

Show config summary:

```sh
ttychatter --config
```

Use demo mode for local testing without an API key:

```sh
ttychatter --demo --prompt "hello"
ttychatter --demo --update-models
ttychatter --demo --models
```

Run the offline regression suites from the project root:

```sh
make check
```

This includes C checks, shell and ncurses regressions, and mailbot tests. The
complete test suite requires `jq` and Python 3 in addition to C build tools. Real
ncurses screen assertions use the optional development dependency `pyte`; those
tests are reported as skipped when it is not installed. No regression test
requires an OpenRouter key, a paid model request, or a live email account.

Mailbot now serializes overlapping polls and saves completed chat replies
before sending mail. A failed delivery can retry the saved reply without
generating it again. This does not guarantee exactly-once SMTP delivery across
a crash after server acceptance. See its walkthrough for queue and timeout
configuration.

## Project discipline

For future changes:

- Update `--help`, man pages, and this README together.
- Keep user-visible settings configurable through config keys and CLI flags.
- Add interactive colon commands when the feature makes sense in an interactive client.
- Keep session logs append-oriented; do not silently mutate history.
- Preserve session IDs across safe physical renames and retain old-name aliases.
- Never overwrite an existing session or response attachment when choosing a name.
- Keep metadata outside conversation memory and honor explicit memory clears.
- Keep XDG paths shared across implementations.
- Do not print API keys in diagnostics.
- Do not describe status updates as model thoughts or hidden reasoning.
