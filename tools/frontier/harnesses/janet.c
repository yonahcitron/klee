/* janet frontier harness — prefix||window, NUL-terminated, janet_dostring. */
#include "frontier_common.h"

#include "janet.h"

#define HARNESS_BUF 4096

int main(int argc, char **argv) {
    static char buf[HARNESS_BUF + 1];
    size_t total = frontier_fill(buf, HARNESS_BUF, argc, argv);
    buf[total] = '\0';

    if (janet_init() != 0) return 2;
    JanetTable *env = janet_core_env(NULL);
    Janet out;
    int rc = janet_dostring(env, buf, "harness", &out);
    janet_deinit();
    return rc == 0 ? 0 : 1;
}
