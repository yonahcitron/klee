/* sqlite frontier harness — prefix||window, NUL-terminated, sqlite3_exec
 * against an in-memory DB. */
#include "frontier_common.h"

#include "sqlite3.h"

#define HARNESS_BUF 4096

int main(int argc, char **argv) {
    static char buf[HARNESS_BUF + 1];
    size_t total = frontier_fill(buf, HARNESS_BUF, argc, argv);
    buf[total] = '\0';

    sqlite3 *db = NULL;
    int rc = sqlite3_open_v2(":memory:", &db,
                             SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, NULL);
    if (rc != SQLITE_OK) {
        if (db) sqlite3_close_v2(db);
        return 2;
    }

    rc = sqlite3_exec(db, buf, NULL, NULL, NULL);
    sqlite3_close_v2(db);
    return rc == SQLITE_OK ? 0 : 1;
}
