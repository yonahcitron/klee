/* yyjson frontier harness — prefix||window, yyjson_read over the exact
 * candidate length. Length-exact subject (default flags demand the whole
 * range be one JSON document): the symbolic window length supplies the
 * "input ends here" forks that the fixed-size -sym-stdin run lacked. */
#include "frontier_common.h"

#include "yyjson.h"

#define HARNESS_BUF 4096

int main(int argc, char **argv) {
    static char buf[HARNESS_BUF];
    size_t total = frontier_fill(buf, HARNESS_BUF, argc, argv);

    yyjson_doc *d = yyjson_read(buf, total, 0);
    if (!d) return 1;
    yyjson_doc_free(d);
    return 0;
}
