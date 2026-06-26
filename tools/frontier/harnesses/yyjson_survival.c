/* yyjson_survival.c — survival classifier for run15 (yyjson, length-exact JSON).
 *
 * Exit code = the parser's control flow, read from yyjson's own error code:
 *   exit 0  accept : yyjson_read_opts returned a document (the whole length-N
 *                    buffer is one valid JSON value — criterion-1 validity)
 *   exit 10 alive  : UNEXPECTED_END / EMPTY_CONTENT — the input ended while more
 *                    was expected (a viable prefix; e.g. `[1,` wants more)
 *   exit 11 dead   : any other parse error (UNEXPECTED_CHARACTER, JSON_STRUCTURE,
 *                    …) — a committed error a longer suffix can't fix
 *
 * Pure parse, no execution — no infinite-loop hazard (unlike an interpreter
 * subject). yyjson's keyword-ish literals (true/false/null) are word-compared
 * (`byte_match_4`), so they do NOT surface as per-byte `Eq` pins — criterion 2
 * is effectively blocked here (a different boundary from luac's hash, same
 * outcome). yyjson is the run15 criterion-1 (length-exact validity) comparison
 * subject with a rich grammar.
 *
 * Native; reads the candidate from stdin. Built with afl-clang-fast so
 * afl-showmap gives the coverage-novelty signal for --unified (see build-run15.sh).
 */
#include <stdio.h>
#include "yyjson.h"

#define BUF (1 << 16)

int main(void) {
    static char buf[BUF];
    size_t n = fread(buf, 1, sizeof buf, stdin);

    yyjson_read_err err;
    yyjson_doc *d = yyjson_read_opts(buf, n, 0, NULL, &err);

    int out;
    if (d) {
        out = 0;
        yyjson_doc_free(d);
    } else if (err.code == YYJSON_READ_ERROR_UNEXPECTED_END ||
               err.code == YYJSON_READ_ERROR_EMPTY_CONTENT) {
        out = 10;
    } else {
        out = 11;
    }
    return out;
}
