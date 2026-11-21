/* Failure-path and streaming regressions. All requests stay offline. */
#define main ttychatter_program_main
#include "../src/ttychatter.c"
#undef main
#include <assert.h>
#include <signal.h>
#include <sys/resource.h>
#include <sys/wait.h>

static void test_atomic_write(const char *directory) {
    char *path = path_join2(directory, "atomic.conf");
    write_file_mode(path, "ORIGINAL=preserved\n", 0600);
    pid_t child = fork();
    assert(child >= 0);
    if (!child) {
        struct rlimit limit = {8, 8};
        assert(signal(SIGXFSZ, SIG_IGN) != SIG_ERR);
        assert(setrlimit(RLIMIT_FSIZE, &limit) == 0);
        write_file_mode(path, "REPLACEMENT=must not destroy original on disk full\n", 0600);
        _exit(0);
    }
    int status;
    assert(waitpid(child, &status, 0) == child);
    assert(WIFEXITED(status) && WEXITSTATUS(status) != 0);
    char *saved = read_file(path, NULL);
    assert(strcmp(saved, "ORIGINAL=preserved\n") == 0);
    free(saved);
    write_file_mode(path, "REPLACED=successfully\n", 0600);
    saved = read_file(path, NULL);
    assert(strcmp(saved, "REPLACED=successfully\n") == 0);
    free(saved);
    struct stat info;
    assert(stat(path, &info) == 0 && (info.st_mode & 0777) == 0600);
    char *shortcut = path_join2(directory, "atomic-link.conf");
    assert(symlink(path, shortcut) == 0);
    write_file_mode(shortcut, "SYMLINK=preserved\n", 0600);
    assert(lstat(shortcut, &info) == 0 && S_ISLNK(info.st_mode));
    saved = read_file(path, NULL);
    assert(strcmp(saved, "SYMLINK=preserved\n") == 0);
    free(saved); free(shortcut); free(path);
}

static void test_long_config_line(const char *directory) {
    TCConfig cfg = {0};
    cfg.config_file = path_join2(directory, "long.conf");
    TCBuffer content;
    buffer_init(&content);
    buffer_append(&content, "MODEL_TEST_PROMPT=");
    for (size_t i = 0; i < 20000; i++) buffer_append(&content, "z");
    buffer_append(&content, "\nMODEL=retained/model\n");
    write_file_mode(cfg.config_file, content.data, 0600);
    assert(config_load_file(&cfg, cfg.config_file));
    assert(strlen(cfg.model_test_prompt) == 20000);
    assert(!strcmp(cfg.model, "retained/model"));
    config_write_key_value(&cfg, "MODEL_TEST_PROMPT", "short replacement");
    char *saved = read_file(cfg.config_file, NULL);
    assert(strcmp(saved, "MODEL_TEST_PROMPT=short replacement\nMODEL=retained/model\n") == 0);
    free(saved);
    write_file_mode(cfg.config_file, content.data, 0600);
    config_remove_key(&cfg, "MODEL_TEST_PROMPT");
    saved = read_file(cfg.config_file, NULL);
    assert(strcmp(saved, "MODEL=retained/model\n") == 0);
    free(saved);
    write_file_mode(cfg.config_file, "MODEL=retained/model", 0600);
    config_write_key_value(&cfg, "STREAM", "1");
    saved = read_file(cfg.config_file, NULL);
    assert(strcmp(saved, "MODEL=retained/model\nSTREAM=1\n") == 0);
    free(saved); free(content.data); free(cfg.config_file);
    free(cfg.model_test_prompt); free(cfg.model);
}

static void test_large_context_limit(void) {
    TCMessageList messages = {0};
    for (size_t i = 0; i < 5; i++) msglist_add(&messages, "user", "retained");
    TCMessageList clipped = msglist_clip_take(&messages, LONG_MAX);
    assert(clipped.count == 5);
    interactive_context_add(&clipped, "assistant", "also retained", LONG_MAX);
    assert(clipped.count == 6);
    TCBuffer log;
    buffer_init(&log);
    append_shared_context_snapshot(&log, "01:02:03", &clipped, LONG_MAX, "latest", "reply");
    TCLogEntryList entries = parse_shared_log_entries(log.data);
    bool parsed = false;
    messages = load_context_from_shared_entries(&entries, LONG_MAX, &parsed);
    assert(parsed && messages.count == 8);
    msglist_free(&messages); msglist_free(&clipped);
    logentries_free(&entries); free(log.data);
}

static void test_snapshot_replay(const char *directory) {
    char *path = path_join2(directory, "snapshots.log");
    write_file_mode(path,
        "[01:02:03] User: old history\n[01:02:03] AI: old answer\n"
        "[01:02:04] system: CONTEXT_SNAPSHOT_JSON [\"User: saved\",\"Assistant: answer\"]\n"
        "[01:02:05] User: newer\n[01:02:05] AI: newer answer\n"
        "[01:02:06] system: CONTEXT_SNAPSHOT_JSON [\"User: unfinished\"\n", 0600);
    TCMessageList messages = load_context_from_session(path, 12);
    assert(messages.count == 4 && !strcmp(messages.items[0].content, "saved"));
    assert(!strcmp(messages.items[3].content, "newer answer"));
    msglist_free(&messages);
    append_file_text(path,
        "[01:02:07] system: CONTEXT_BEGIN\nUser: edited\n  second line\nAssistant: edited answer\n"
        "[01:02:08] system: CONTEXT_END\n");
    messages = load_context_from_session(path, 12);
    assert(messages.count == 2 && !strcmp(messages.items[0].content, "edited\n  second line"));
    msglist_free(&messages);
    append_file_text(path, "[01:02:09] system: CONTEXT_SNAPSHOT_JSON []\n");
    messages = load_context_from_session(path, 12);
    assert(messages.count == 0);
    msglist_free(&messages); free(path);
}

static void dispose_response(TCHttpResponse *response, TCStreamState *state) {
    free(response->body);
    if (response->metadata) json_object_put(response->metadata);
    streamstate_free(state);
}

static void test_stream_boundaries(void) {
    const char *endings[] = {"\n", "\r\n", "\r"};
    for (size_t ending = 0; ending < 3; ending++) {
        TCBuffer wire;
        buffer_init(&wire);
        const char *e = endings[ending];
        buffer_appendf(&wire, ": heartbeat%s%sdata: {\"choices\":[{\"delta\":{\"content\":\"answer\"}}]}%s%sdata: [DONE]%s%s", e, e, e, e, e, e);
        for (size_t width = 1; width < 12; width++) {
            TCStreamState state;
            streamstate_init(&state, false);
            for (size_t pos = 0; pos < wire.len; pos += width) {
                size_t count = wire.len - pos < width ? wire.len - pos : width;
                assert(curl_sse_write_cb(wire.data + pos, 1, count, &state) == count);
            }
            assert(state.done);
            TCHttpResponse response = sse_finish_response(&state, CURLE_OK, 200);
            assert(!response.failed && strcmp(response.body, "answer") == 0);
            dispose_response(&response, &state);
        }
        free(wire.data);
    }

    /* A completed event must be terminal even if more bytes are buffered. */
    const char *wire = "data: {\"choices\":[{\"delta\":{\"content\":\"final\"}}]}\n\n"
                       "data: [DONE]\n\n"
                       "data: {\"choices\":[{\"delta\":{\"content\":\"stray\"}}]}\n\n";
    TCStreamState state;
    streamstate_init(&state, false);
    assert(curl_sse_write_cb((void *)wire, 1, strlen(wire), &state) == strlen(wire));
    assert(state.done && strcmp(state.text.data, "final") == 0);
    assert(curl_sse_xferinfo_cb(&state, 0, 0, 0, 0) != 0);
    TCHttpResponse response = sse_finish_response(&state, CURLE_ABORTED_BY_CALLBACK, 200);
    assert(!response.failed && strcmp(response.body, "final") == 0);
    dispose_response(&response, &state);
}

static void test_stream_event_limit(void) {
    TCBuffer wire;
    buffer_init(&wire);
    buffer_append(&wire, "data: {\"choices\":[{\"delta\":{\"content\":\"");
    size_t payload = 8 * 1024 * 1024 + 1;
    buffer_reserve(&wire, payload);
    memset(wire.data + wire.len, 'x', payload);
    wire.len += payload;
    wire.data[wire.len] = '\0';
    buffer_append(&wire, "\"}}]}\n\n");
    TCStreamState state;
    streamstate_init(&state, false);
    assert(curl_sse_write_cb(wire.data, 1, wire.len, &state) == 0);
    assert(state.error && strstr(state.error, "8 MiB"));
    assert(state.text.len == 0);
    TCHttpResponse response = sse_finish_response(&state, CURLE_WRITE_ERROR, 200);
    assert(response.failed && strstr(response.body, "8 MiB"));
    dispose_response(&response, &state);
    /* Also cross the bound only when the event's final network chunk arrives. */
    streamstate_init(&state, false);
    size_t prefix = wire.len - 128;
    assert(prefix < 8 * 1024 * 1024);
    assert(curl_sse_write_cb(wire.data, 1, prefix, &state) == prefix);
    assert(curl_sse_write_cb(wire.data + prefix, 1, 128, &state) == 0);
    assert(state.error && state.text.len == 0);
    streamstate_free(&state);
    free(wire.data);
}

static void test_stream_exact_event_limit(void) {
    const char *prefix = "data: {\"choices\":[{\"delta\":{\"content\":\"";
    const char *suffix = "\"}}]}";
    const size_t limit = 8 * 1024 * 1024;
    for (size_t extra = 0; extra <= 1; extra++) {
        TCBuffer wire;
        buffer_init(&wire);
        buffer_append(&wire, prefix);
        size_t payload = limit + extra - strlen(prefix) - strlen(suffix);
        buffer_reserve(&wire, payload);
        memset(wire.data + wire.len, 'x', payload);
        wire.len += payload;
        wire.data[wire.len] = '\0';
        buffer_append(&wire, suffix);
        size_t event_size = wire.len;
        assert(event_size == limit + extra);
        buffer_append(&wire, "\n\n");
        for (int split = 0; split < 2; split++) {
            TCStreamState state;
            streamstate_init(&state, false);
            size_t received = curl_sse_write_cb(wire.data, 1, split ? event_size : wire.len, &state);
            if (extra) {
                assert(received == 0 && state.error && state.text.len == 0);
            } else {
                assert(received == (split ? event_size : wire.len));
                if (split) {
                    /* The first delimiter byte is not part of the event. */
                    assert(curl_sse_write_cb("\n", 1, 1, &state) == 1);
                    assert(curl_sse_write_cb("\n", 1, 1, &state) == 1);
                }
                assert(!state.error && state.text.len == payload);
                const char *done = "data: [DONE]\n\n";
                assert(curl_sse_write_cb((void *)done, 1, strlen(done), &state) == strlen(done));
                TCHttpResponse response = sse_finish_response(&state, CURLE_OK, 200);
                assert(!response.failed && strlen(response.body) == payload);
                free(response.body);
                if (response.metadata) json_object_put(response.metadata);
            }
            streamstate_free(&state);
        }
        free(wire.data);
    }
}

int main(int argc, char **argv) {
    assert(argc >= 2);
    const char *test = argc > 2 ? argv[2] : "all";
    if (!strcmp(test, "http") || !strcmp(test, "http-cancel") || !strcmp(test, "http-truncated")) {
        bool cancel = !strcmp(test, "http-cancel");
        TCHttpResponse response = http_request_stream(argv[1], NULL, "{}", cancel);
        if (!strcmp(test, "http-truncated")) {
            assert(response.failed && strstr(response.body, "partial"));
            assert(strstr(response.body, "before [DONE]"));
        } else {
            assert(!response.failed && response.status == 200);
            if (cancel) assert(strstr(response.body, TC_STREAM_INTERRUPT_MARKER));
            else assert(!strcmp(response.body, "answer"));
        }
        free(response.body);
        if (response.metadata) json_object_put(response.metadata);
        return 0;
    }
    if (!strcmp(test, "all") || !strcmp(test, "write")) test_atomic_write(argv[1]);
    if (!strcmp(test, "all") || !strcmp(test, "config")) test_long_config_line(argv[1]);
    if (!strcmp(test, "all") || !strcmp(test, "context")) test_large_context_limit();
    if (!strcmp(test, "all") || !strcmp(test, "snapshot")) test_snapshot_replay(argv[1]);
    if (!strcmp(test, "all") || !strcmp(test, "stream")) test_stream_boundaries();
    if (!strcmp(test, "all") || !strcmp(test, "limit")) {
        test_stream_event_limit();
        test_stream_exact_event_limit();
    }
    puts("C stability and streaming regressions passed");
    return 0;
}
