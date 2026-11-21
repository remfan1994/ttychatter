/* Offline protocol and data-preservation regressions. No network requests. */
#define main ttychatter_program_main
#include "../src/ttychatter.c"
#undef main
#include <assert.h>
#include <sys/wait.h>

static json_object *member(json_object *obj, const char *key) {
    json_object *value = NULL;
    assert(json_object_object_get_ex(obj, key, &value));
    return value;
}

static void free_response(TCHttpResponse *response) {
    free(response->body);
    if (response->metadata) json_object_put(response->metadata);
}

static void test_payload(TCConfig *cfg) {
    TCArgs args = {0};
    TCMessageList context = {0};
    args.session_id = "0123456789abcdef0123456789abcdef";
    json_object *request = build_chat_request(cfg, "example/model", "question", &context, &args);
    json_object *unused = NULL;
    assert(!json_object_object_get_ex(request, "tools", &unused));
    assert(strcmp(json_object_get_string(member(request, "session_id")), args.session_id) == 0);
    json_object_put(request);
    args.web_set = true;
    args.web_search = true;
    args.web_search_engine = "perplexity";
    args.web_search_max_results = 25;
    args.web_search_max_uses = 7;
    args.web_search_max_results_set = true;
    args.web_search_max_uses_set = true;
    request = build_chat_request(cfg, "example/model", "question", &context, &args);
    json_object *tool = json_object_array_get_idx(member(request, "tools"), 0);
    assert(strcmp(json_object_get_string(member(tool, "type")), "openrouter:web_search") == 0);
    json_object *parameters = member(tool, "parameters");
    assert(strcmp(json_object_get_string(member(parameters, "engine")), "perplexity") == 0);
    assert(json_object_get_int(member(parameters, "max_results")) == 20);
    assert(!json_object_object_get_ex(parameters, "max_total_results", &unused));
    assert(!json_object_object_get_ex(parameters, "max_uses", &unused));
    assert(json_object_get_int(member(request, "max_tool_calls")) == 7);
    assert(!json_object_object_get_ex(request, "plugins", &unused));
    json_object_put(request);
    args.web_search_max_results = 0;
    args.web_search_max_uses = 0;
    request = build_chat_request(cfg, "example/model", "question", &context, &args);
    tool = json_object_array_get_idx(member(request, "tools"), 0);
    parameters = member(tool, "parameters");
    assert(!json_object_object_get_ex(parameters, "max_results", &unused));
    assert(!json_object_object_get_ex(request, "max_tool_calls", &unused));
    json_object_put(request);
    cfg->web_search = true;
    args.web_search = false;
    request = build_chat_request(cfg, "example/model", "question", &context, &args);
    assert(!json_object_object_get_ex(request, "tools", &unused));
    json_object_put(request);
    cfg->web_search = false;
    assert(bounded_number("1garbage", 0, 25) == LONG_MIN);
    assert(bounded_number("0", 0, 25) == 0);
    assert(bounded_number("26", 0, 25) == LONG_MIN);
    assert(!valid_web_engine("made-up"));
}

static const char *response_json =
    "{\"id\":\"gen-example\",\"model\":\"example/model\",\"provider\":\"example-provider\",\"usage\":{\"total_tokens\":9},"
    "\"annotations\":[{\"type\":\"url_citation\",\"url_citation\":{\"url\":\"https://example.org/a\",\"title\":\"First source\"}}],"
    "\"choices\":[{\"message\":{\"content\":[{\"type\":\"text\",\"text\":\"Verified answer.\"}],"
    "\"annotations\":[{\"type\":\"url_citation\",\"url_citation\":{\"url\":\"https://example.org/a\",\"title\":\"First source\"}},"
    "{\"type\":\"url_citation\",\"url_citation\":{\"url\":\"https://example.org/b\",\"title\":\"Second source\"}}],"
    "\"reasoning_details\":[{\"type\":\"reasoning.encrypted\",\"data\":\"opaque-example\"}]}}]}";

static void test_response_and_context(TCConfig *cfg) {
    TCArgs args = {0};
    args.ai_file_download_mode = "metadata";
    char *reply = parse_chat_response(cfg, &args, response_json);
    assert(!args.request_failed);
    assert(strstr(reply, "Verified answer."));
    assert(strstr(reply, "[1] First source"));
    assert(strstr(reply, "[2] Second source"));
    assert(!strstr(reply, "[3]"));
    assert(!strstr(reply, "opaque-example"));
    assert(json_object_get_int(member(member(args.response_metadata, "usage"), "total_tokens")) == 9);
    assert(strcmp(json_object_get_string(member(args.response_metadata, "provider")), "example-provider") == 0);
    char *path = default_session_path(cfg);
    maybe_write_prechat_and_startup_notice(cfg, &args, path);
    TCMessageList context = {0};
    append_turn(path, "example/model", "test", &args, "first question\nsecond line", reply, &context, 12);
    free(reply);
    context = load_context_from_session(path, 12);
    assert(context.count == 2);
    assert(strcmp(context.items[0].content, "first question\nsecond line") == 0);
    assert(!strstr(context.items[1].content, "opaque-example"));
    assert(strstr(context.items[1].content, "https://example.org/a"));
    reply = parse_chat_response(cfg, &args, "{\"error\":{\"message\":\"server temporarily unavailable\"}}");
    assert(args.request_failed);
    append_turn(path, "example/model", "test", &args, "failed question", reply, &context, 12);
    TCMessageList reloaded = load_context_from_session(path, 12);
    assert(reloaded.count == 2);
    assert(strstr(reloaded.items[0].content, "first question"));
    assert(!strstr(reloaded.items[1].content, "temporarily unavailable"));
    char *log = read_file(path, NULL);
    assert(strstr(log, "openrouter-response:"));
    assert(strstr(log, "request-error:"));
    assert(strstr(log, "failed question"));
    free(log); free(reply); free(path);
    msglist_free(&context); msglist_free(&reloaded);
    json_object_put(args.response_metadata);
}

static void feed_chunks(TCStreamState *state, const char *wire, size_t width) {
    size_t len = strlen(wire);
    for (size_t pos = 0; pos < len; pos += width) {
        size_t count = len - pos < width ? len - pos : width;
        assert(curl_sse_write_cb((void *)(wire + pos), 1, count, state) == count);
    }
}

static void test_streams(void) {
    const char *wire =
        ": keep alive\r\n\r\n"
        "data: {\"id\":\"gen-stream\",\"choices\":[{\"delta\":{\"content\":\"Hello \"}}]}\r\n\r\n"
        "data: {\"choices\":[{\"delta\":{\"content\":[{\"type\":\"text\",\"text\":\"world\"}]},\"finish_reason\":\"stop\"}]}\r\n\r\n"
        "data: {\"choices\":[],\"usage\":{\"total_tokens\":42},\"annotations\":[{\"type\":\"url_citation\",\"url_citation\":{\"url\":\"https://example.org/late\",\"title\":\"Late source\"}}]}\r\n\r\n"
        "data: [DONE]\r\n\r\n";
    for (size_t width = 1; width < 30; width += 7) {
        TCStreamState st;
        streamstate_init(&st, false);
        feed_chunks(&st, wire, width);
        assert(st.done);
        TCHttpResponse response = sse_finish_response(&st, CURLE_OK, 200);
        assert(!response.failed);
        assert(strstr(response.body, "Hello world"));
        assert(strstr(response.body, "https://example.org/late"));
        assert(json_object_get_int(member(member(response.metadata, "usage"), "total_tokens")) == 42);
        free_response(&response); streamstate_free(&st);
    }
    TCStreamState st;
    streamstate_init(&st, false);
    feed_chunks(&st, "data: {\"choices\":[{\"delta\":{\"content\":\"Keep my partial answer\"}}]}\n\n", 9);
    TCHttpResponse response = sse_finish_response(&st, CURLE_RECV_ERROR, 200);
    assert(response.failed && strstr(response.body, "Keep my partial answer"));
    assert(strstr(response.body, "stream error:"));
    free_response(&response); streamstate_free(&st);
    streamstate_init(&st, false);
    feed_chunks(&st, "data: {\"choices\":[{\"delta\":{\"content\":\"Partial\"}}]}\n\n", 4);
    st.cancelled = true;
    response = sse_finish_response(&st, CURLE_WRITE_ERROR, 0);
    assert(!response.failed && response.status == 200);
    assert(strstr(response.body, TC_STREAM_INTERRUPT_MARKER));
    free_response(&response); streamstate_free(&st);
    streamstate_init(&st, false);
    feed_chunks(&st, "data: {\"error\":{\"message\":\"search engine unavailable\"}}\n\ndata: [DONE]\n\n", 5);
    response = sse_finish_response(&st, CURLE_OK, 200);
    assert(response.failed && strstr(response.body, "search engine unavailable"));
    free_response(&response); streamstate_free(&st);
    streamstate_init(&st, false);
    feed_chunks(&st, "data: invalid-json\n\ndata: [DONE]\n\n", 3);
    response = sse_finish_response(&st, CURLE_OK, 200);
    assert(response.failed && strstr(response.body, "invalid JSON"));
    free_response(&response); streamstate_free(&st);
    streamstate_init(&st, false);
    feed_chunks(&st, "data: {\"choices\":[{\"delta\":{\"content\":\"Truncated\"},\"finish_reason\":\"stop\"}]}\n\n", 11);
    response = sse_finish_response(&st, CURLE_OK, 200);
    assert(response.failed && strstr(response.body, "before [DONE]"));
    free_response(&response); streamstate_free(&st);
}

static void test_shared_snapshot_order(TCConfig *cfg) {
    char *path = default_session_path(cfg);
    append_file_text(path,
        "[01:00:00] User: original\n[01:00:01] AI: original answer\n"
        "[01:00:02] system: CONTEXT_SNAPSHOT_JSON [\"User: json edit\", \"Assistant: json answer\"]\n"
        "[01:00:03] User: after json\n[01:00:04] AI: later answer\n");
    TCMessageList ctx = load_context_from_session(path, 12);
    assert(ctx.count == 4 && strcmp(ctx.items[0].content, "json edit") == 0);
    assert(strstr(ctx.items[3].content, "later answer"));
    msglist_free(&ctx);
    append_file_text(path,
        "[01:00:05] system: CONTEXT_BEGIN\nUser: text edit\nsecond line\n  indented\nAssistant: text answer\n"
        "[01:00:06] system: CONTEXT_END\n"
        "[01:00:07] User: after text\n[01:00:08] AI: newest answer\n"
        "[01:00:09] system: openrouter-response: {\"usage\":{\"total_tokens\":3}}\n");
    ctx = load_context_from_session(path, 12);
    assert(ctx.count == 4 && strcmp(ctx.items[0].content, "text edit\nsecond line\n  indented") == 0);
    assert(strstr(ctx.items[3].content, "newest answer"));
    msglist_free(&ctx);
    append_file_text(path, "[01:00:10] system: CONTEXT_SNAPSHOT_JSON []\n[01:00:11] User: after clear\n");
    ctx = load_context_from_session(path, 12);
    assert(ctx.count == 1 && strstr(ctx.items[0].content, "after clear"));
    msglist_free(&ctx);
    append_file_text(path,
        "[01:00:12] system: CONTEXT_BEGIN\nUser: incomplete edit\nCONTEXT_END_not_a_marker\n"
        "[01:00:13] User: after incomplete snapshot\n"
        "[01:00:14] system: CONTEXT_SNAPSHOT_JSON {malformed}\n");
    ctx = load_context_from_session(path, 12);
    assert(ctx.count == 2 && strstr(ctx.items[0].content, "after clear"));
    assert(strstr(ctx.items[1].content, "after incomplete snapshot"));
    msglist_free(&ctx); free(path);

    path = default_session_path(cfg);
    append_file_text(path,
        TC_TURN_BEGIN "\n" TC_USER_BEGIN "\nlegacy user\n" TC_USER_END "\n"
        TC_AI_BEGIN "\nlegacy answer\n" TC_AI_END "\n" TC_TURN_END "\n");
    char *id = ensure_session_id(path);
    free(id);
    ctx = load_context_from_session(path, 12);
    assert(ctx.count == 2 && strstr(ctx.items[0].content, "legacy user"));
    msglist_free(&ctx);
    append_file_text(path, TC_CONTEXT_BEGIN "\nUser: legacy edit\nsecond line\nAssistant: legacy answer\n" TC_CONTEXT_END "\n");
    ctx = load_context_from_session(path, 12);
    assert(ctx.count == 2 && strcmp(ctx.items[0].content, "legacy edit\nsecond line") == 0);
    msglist_free(&ctx); free(path);

    path = default_session_path(cfg);
    append_file_text(path,
        "[01:00:00] remfan1994: " TC_PRECHAT_LINE "\n\n"
        "[01:00:01] remfan1994: real user message\n"
        "[01:00:02] AI: real reply\n");
    ctx = load_context_from_session(path, 12);
    assert(ctx.count == 2 && strcmp(ctx.items[0].content, "real user message") == 0);
    msglist_free(&ctx);
    ctx = load_messages_from_session_file(path);
    assert(ctx.count == 2 && strcmp(ctx.items[0].content, "real user message") == 0);
    msglist_free(&ctx); free(path);
}

static void test_memory_writes(TCConfig *cfg) {
    char *path = default_session_path(cfg);
    assert(write_context_snapshot_file(path, "User: revised\nsecond line\n  indented\nAssistant: reply"));
    TCMessageList ctx = load_context_from_session(path, 12);
    assert(ctx.count == 2 && strcmp(ctx.items[0].content, "revised\nsecond line\n  indented") == 0);
    msglist_free(&ctx);
    assert(write_context_snapshot_file(path, "[\"User: first\\nUser: literal role label\\n\\n  last \",\"Assistant: answer\"]"));
    ctx = load_context_from_session(path, 12);
    assert(ctx.count == 2 && strcmp(ctx.items[0].content, "first\nUser: literal role label\n\n  last ") == 0);
    msglist_free(&ctx);
    long original_size = file_size(path);
    assert(!write_context_snapshot_file(path, "[{}]"));
    assert(file_size(path) == original_size);
    assert(!write_context_snapshot_file(path, "unlabeled accidental text"));
    assert(file_size(path) == original_size);
    assert(clear_memory_command(path) == 0);
    ctx = load_context_from_session(path, 12);
    assert(ctx.count == 0);
    msglist_free(&ctx);
    char *saved = read_file(path, NULL);
    assert(!strstr(saved, "CONTEXT_BEGIN"));
    assert(strstr(saved, "CONTEXT_SNAPSHOT_JSON []"));
    free(saved); free(path);
}

static void test_sessions(TCConfig *cfg) {
    TCArgs args = {0};
    char *path = default_session_path(cfg);
    maybe_write_prechat_and_startup_notice(cfg, &args, path);
    char *id = ensure_session_id(path);
    assert(valid_session_id(id));
    char *again = ensure_session_id(path);
    assert(strcmp(id, again) == 0);
    free(again);
    char leaf[128];
    snprintf(leaf, sizeof(leaf), "Friendly-Notes--%.12s.log", id);
    char *occupied = path_join2(cfg->session_dir, leaf);
    append_file_text(occupied, "Other session must remain untouched.\n");
    char *renamed = rename_session_file(path, "Friendly Notes");
    assert(renamed && !file_exists(path));
    assert(strstr(renamed, "-001.log"));
    char *other = read_file(occupied, NULL);
    assert(strcmp(other, "Other session must remain untouched.\n") == 0);
    free(other);
    again = ensure_session_id(renamed);
    assert(strcmp(id, again) == 0);
    free(again);
    char *resolved = session_path_from_arg(cfg, "Friendly Notes");
    assert(strcmp(resolved, renamed) == 0);
    free(resolved);
    const char *long_title = "Title: This is the complete title with punctuation. It is deliberately longer than ninety-six characters and remains complete in the session metadata, including its final word: PRESERVED.";
    char *second = rename_session_file(renamed, long_title);
    assert(second);
    char *title = session_title_from_file(second);
    assert(strcmp(title, long_title) == 0);
    free(title);
    resolved = session_path_from_arg(cfg, "Friendly Notes");
    assert(strcmp(resolved, second) == 0);
    free(resolved);
    char *old_stem = basename_no_ext(path);
    resolved = session_path_from_arg(cfg, old_stem);
    assert(strcmp(resolved, second) == 0);
    free(resolved); free(old_stem);
    char *old_leaf = strrchr(renamed, '/');
    resolved = session_path_from_arg(cfg, old_leaf ? old_leaf + 1 : renamed);
    assert(strcmp(resolved, second) == 0);
    free(resolved);
    struct stat info;
    assert(stat(second, &info) == 0 && (info.st_mode & 0777) == 0600);
    char *symlink_path = path_join2(cfg->session_dir, "shortcut.log");
    assert(symlink(second, symlink_path) == 0);
    assert(rename_session_file(symlink_path, "Should fail") == NULL);
    assert(file_exists(second));
    TCMessageList msgs = {0};
    char *branch = create_branch_file_from_prefix(cfg, &args, second, &msgs, 0, 0, "test");
    char *branch_id = ensure_session_id(branch);
    assert(strcmp(branch_id, id) != 0);
    free(branch_id); free(branch); free(symlink_path); free(second); free(renamed); free(occupied); free(path); free(id);

    /* Two processes opening an old log must agree on a single ID. */
    char *shared = path_join2(cfg->session_dir, "legacy-shared.log");
    append_file_text(shared, TC_PRECHAT_LINE "\n[12:00:00] User: legacy");
    pid_t children[4];
    for (int i = 0; i < 4; i++) {
        children[i] = fork();
        assert(children[i] >= 0);
        if (!children[i]) { char *child_id = ensure_session_id(shared); free(child_id); _exit(0); }
    }
    for (int i = 0; i < 4; i++) { int status; assert(waitpid(children[i], &status, 0) == children[i]); assert(WIFEXITED(status) && WEXITSTATUS(status) == 0); }
    char *contents = read_file(shared, NULL);
    char *marker = strstr(contents, "session-id:");
    assert(marker && !strstr(marker + 1, "session-id:"));
    TCMessageList legacy = load_context_from_session(shared, 12);
    assert(legacy.count == 1 && strcmp(legacy.items[0].content, "legacy") == 0);
    msglist_free(&legacy);
    free(contents); free(shared);
}

static void test_attachments(TCConfig *cfg) {
    const char *encoded[] = {"YQ==", "YWI=", "YWJj", "aGVsbG8=", "aGVs bG8=\n", "aGVsbG8", NULL};
    const char *decoded[] = {"a", "ab", "abc", "hello", "hello", "hello"};
    for (size_t i = 0; encoded[i]; i++) {
        size_t length = 0;
        unsigned char *data = base64_decode_alloc(encoded[i], &length);
        assert(data && length == strlen(decoded[i]) && memcmp(data, decoded[i], length) == 0);
        free(data);
    }
    size_t length = 99;
    assert(base64_decode_alloc("YQ==more", &length) == NULL && length == 0);
    assert(base64_decode_alloc("***", &length) == NULL);
    cfg->code_attachment_min_lines = 1;
    char *first = extract_code_blocks(cfg, "same-session", "```txt\nfirst-file-contents\n```\n");
    char *second = extract_code_blocks(cfg, "same-session", "```txt\nsecond-file-contents\n```\n");
    assert(strstr(first, "code-01"));
    assert(strstr(second, "code-02"));
    char *first_path = path_join2(cfg->attachment_dir, "same-session-code-01.txt");
    char *contents = read_file(first_path, NULL);
    assert(strcmp(contents, "first-file-contents\n") == 0);
    free(contents); free(first_path); free(first); free(second);
    TCAIFile file = {0};
    file.name = "received.txt";
    file.mime = "text/plain";
    file.data = "aGVsbG8=";
    char *path1 = NULL, *path2 = NULL, *error = NULL;
    assert(write_ai_file_payload(cfg, &file, &path1, &error));
    assert(write_ai_file_payload(cfg, &file, &path2, &error));
    assert(strcmp(path1, path2) != 0);
    contents = read_file(path1, NULL);
    assert(strcmp(contents, "hello") == 0);
    free(contents); free(path1); free(path2); free(error);
}

int main(int argc, char **argv) {
    assert(argc == 2);
    TCConfig cfg;
    config_init_defaults(&cfg);
    free(cfg.session_dir); cfg.session_dir = path_join2(argv[1], "sessions");
    free(cfg.attachment_dir); cfg.attachment_dir = path_join2(argv[1], "attachments");
    mkdir_p(cfg.session_dir); mkdir_p(cfg.attachment_dir);
    test_payload(&cfg);
    test_response_and_context(&cfg);
    test_streams();
    test_shared_snapshot_order(&cfg);
    test_memory_writes(&cfg);
    test_sessions(&cfg);
    test_attachments(&cfg);
    puts("C offline protocol, session, and attachment regressions passed");
    return 0;
}
