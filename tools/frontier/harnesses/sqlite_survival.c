/* sqlite_survival.c — native survival classifier for the SQL parser subject
 * (sqlite, parse-only via sqlite3_prepare_v2 — no execution, no side effects).
 *
 * Exit code is the true survival signal, read from sqlite's own parser control
 * flow as we walk the input statement-by-statement:
 *   exit 0  = accept  (every sqlite3_prepare_v2 succeeded to end-of-input: the
 *                      whole buffer is a valid, complete sequence of SQL stmts)
 *   exit 10 = alive   (a prepare failed because the parser ran out of input
 *                      mid-statement — a viable prefix; e.g. `CREATE TABLE t(`)
 *   exit 11 = dead    (a prepare failed on a present byte — a committed error a
 *                      longer suffix can't fix; e.g. `SELET 1 1 1`)
 *
 * Detecting alive vs dead — the signal: sqlite's parser emits the exact error
 * message "incomplete input" when, and only when, it hit end-of-input while a
 * statement was still open (its EOF / "wants more" path). Every other parse
 * failure is a committed error on a present byte ("near X: syntax error",
 * "unrecognized token", "incomplete input" never). This is sqlite's own
 * "should I read another line?" test — the same distinction the sqlite3 shell
 * uses for its continuation prompt.
 *
 * NB the contract sketches sqlite3_complete() for this, but that only tests for
 * a trailing ';' terminator: it returns 0 for BOTH `CREATE TABLE t(` (alive)
 * and `SELET 1 1 1` (dead), so it cannot separate the two — it would call the
 * garbage case alive(10). sqlite exposes no extended result code for the
 * incomplete-input case either (both are plain SQLITE_ERROR == 1). The error
 * message is the only clean discriminator, so we key on it. (Empirically
 * verified across accept/alive/dead cases against this amalgamation.)
 *
 * PARSE-ONLY: sqlite3_prepare_v2 compiles the statement to bytecode but never
 * runs it (no sqlite3_exec, no sqlite3_step) — no filesystem, no execution
 * hazard. The DB is :memory:.
 *
 * Build: afl-clang-fast / clang -O2 -w sqlite_survival.c sqlite3.c -I<dir>
 *        -lpthread -ldl -lm   (see build-run15.sh's pattern / the task script).
 */
#include <stddef.h>
#include <string.h>
#include <unistd.h>

#include "sqlite3.h"

#define HARNESS_BUF (1 << 20)

int main(void) {
    static char buf[HARNESS_BUF];
    ssize_t total = 0, n;
    while (total < HARNESS_BUF - 1 &&
           (n = read(0, buf + total, HARNESS_BUF - 1 - total)) > 0) {
        total += n;
    }
    buf[total] = '\0';

    sqlite3 *db = NULL;
    int rc = sqlite3_open_v2(":memory:", &db,
                             SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, NULL);
    if (rc != SQLITE_OK) {
        if (db) sqlite3_close_v2(db);
        return 2;
    }

    /* Walk statements: prepare one, finalize it, advance to the tail, repeat.
     * Parse only — we never step/execute the compiled statement. */
    const char *sql = buf;
    int out = 0;                              /* accept, unless a prepare fails */
    while (*sql) {
        sqlite3_stmt *stmt = NULL;
        const char *tail = NULL;
        rc = sqlite3_prepare_v2(db, sql, -1, &stmt, &tail);
        sqlite3_finalize(stmt);
        if (rc != SQLITE_OK) {
            const char *msg = sqlite3_errmsg(db);
            /* "incomplete input" == sqlite's parser ran out mid-statement
             * (alive, viable prefix); any other failure is committed (dead). */
            out = (msg && strcmp(msg, "incomplete input") == 0) ? 10 : 11;
            break;
        }
        if (tail == sql) break;   /* no progress (trailing comment/blank) */
        sql = tail;
    }

    sqlite3_close_v2(db);
    return out;
}
