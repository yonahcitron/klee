/* inih frontier harness — prefix||window, NUL-terminated, ini_parse_string. */
#include "frontier_common.h"

#include "ini.h"

#define HARNESS_BUF 4096

static int sink(void *user, const char *section, const char *name,
                const char *value) {
    (void)user; (void)section; (void)name; (void)value;
    return 1; /* keep parsing */
}

int main(int argc, char **argv) {
    static char buf[HARNESS_BUF + 1];
    size_t total = frontier_fill(buf, HARNESS_BUF, argc, argv);
    buf[total] = '\0';

    int rc = ini_parse_string(buf, sink, NULL);
    return rc == 0 ? 0 : 1;
}
