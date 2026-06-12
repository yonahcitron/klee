/* cJSON frontier harness — prefix||window via frontier_fill, then
 * cJSON_ParseWithLength over exactly that many bytes (length-bounded:
 * the candidate's length is the input's length, no NUL dependence). */
#include "frontier_common.h"

#include "cJSON.h"

#define HARNESS_BUF 4096

int main(int argc, char **argv) {
    static char buf[HARNESS_BUF];
    size_t total = frontier_fill(buf, HARNESS_BUF, argc, argv);

    cJSON *j = cJSON_ParseWithLength(buf, total);
    if (!j) return 1;
    cJSON_Delete(j);
    return 0;
}
