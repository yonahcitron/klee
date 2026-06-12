/* tomlc99 frontier harness — prefix||window, NUL-terminated, toml_parse
 * (which mutates its buffer — buf is writable). */
#include "frontier_common.h"

#include "toml.h"

#define HARNESS_BUF 4096

int main(int argc, char **argv) {
    static char buf[HARNESS_BUF + 1];
    size_t total = frontier_fill(buf, HARNESS_BUF, argc, argv);
    buf[total] = '\0';

    char errbuf[256] = {0};
    toml_table_t *t = toml_parse(buf, errbuf, sizeof errbuf);
    if (!t) return 1;
    toml_free(t);
    return 0;
}
