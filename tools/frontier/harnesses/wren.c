/* wren frontier harness — prefix||window, NUL-terminated, wrenInterpret. */
#include "frontier_common.h"

#include "wren.h"

#define HARNESS_BUF 4096

static void write_noop(WrenVM *vm, const char *text) {
    (void)vm; (void)text;
}

static void error_noop(WrenVM *vm, WrenErrorType type, const char *module,
                       int line, const char *message) {
    (void)vm; (void)type; (void)module; (void)line; (void)message;
}

int main(int argc, char **argv) {
    static char buf[HARNESS_BUF + 1];
    size_t total = frontier_fill(buf, HARNESS_BUF, argc, argv);
    buf[total] = '\0';

    WrenConfiguration cfg;
    wrenInitConfiguration(&cfg);
    cfg.writeFn = write_noop;
    cfg.errorFn = error_noop;

    WrenVM *vm = wrenNewVM(&cfg);
    WrenInterpretResult r = wrenInterpret(vm, "main", buf);
    wrenFreeVM(vm);
    return r == WREN_RESULT_SUCCESS ? 0 : 1;
}
