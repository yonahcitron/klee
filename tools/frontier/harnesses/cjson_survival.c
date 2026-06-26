/* cjson_survival.c — survival classifier for the cJSON subject (length-exact
 * JSON, parsed via cJSON_ParseWithLength).
 *
 * Exit code = the parser's control flow:
 *   exit 0  accept : cJSON_ParseWithLength returned a value (the whole length-N
 *                    buffer is one valid JSON value — criterion-1 validity)
 *   exit 10 alive  : the parse failed BECAUSE input ended while more was
 *                    expected — a viable prefix (e.g. `{"a":`, `[1,`, a lone
 *                    `{`). The "wants more" signal.
 *   exit 11 dead   : the parse failed on a byte that is present (a committed
 *                    error no suffix fixes — e.g. `}{`, `:`, `@`).
 *
 * WHY NOT A BARE cJSON_GetErrorPtr() POSITION TEST (the obvious design):
 *   cJSON exposes the failure point via cJSON_GetErrorPtr() = value + position.
 *   The intent of "ran off the end" is position == buffer_length (the pointer
 *   sits AT the end). But cJSON's parser CLAMPS that pointer on failure:
 *       local_error.position = (offset < length) ? offset : length - 1;
 *   (cJSON.c, cJSON_ParseWithLengthOpts fail: block). So a truncated construct,
 *   whose raw offset reached `length`, is reported one byte short, at
 *   `length - 1` — never `>= length`. A literal `errptr >= buf + n` test would
 *   therefore mark every non-empty truncated input as dead, and at n==1 the
 *   clamp makes a genuine present-byte error (`}`) indistinguishable from an
 *   off-end one (`{`). cJSON_ParseWithLengthOpts' return_parse_end is clamped
 *   identically, so no public accessor exposes the unclamped offset. The bare
 *   position test the contract describes is thus unimplementable against
 *   cJSON's public API as written.
 *
 * HOW WE RECOVER THE SIGNAL (single trailing-whitespace differential, all via
 * the public API): re-parse the buffer with one extra ' ' appended. A space is
 * never the start of a new JSON token, so it cannot turn a committed error into
 * a valid parse spuriously — but it gives the scanner a real in-bounds byte at
 * the old end position. If the input had run off the end, the failure frontier
 * now advances to >= the original length (or the parse accepts, e.g. a number
 * like `123` that was waiting to see if more digits followed). If the input
 * failed on a present byte strictly inside, the trailing space changes nothing
 * and the failure offset is unchanged. Decision:
 *     alive iff  n == 0  ||  padded parse accepts  ||  padded errpos > orig
 *                                                   ||  padded errpos >= n
 * This reproduces the unclamped `offset == length` ("pointer at/past end")
 * condition the contract intends, and passes the required cases exactly:
 * `{"a":[1,2]}`->0, `{"a":`->10, `}{`->11. (Validated over a wider battery in
 * build notes; e.g. `tru`->alive since appending `e` completes `true`.)
 *
 * Pure parse, no execution — no infinite-loop hazard. Native; reads the
 * candidate from stdin. Built with afl-clang-fast so afl-showmap gives the
 * coverage-novelty signal (see build-run15.sh / build-frontier.sh).
 */
#include <stdio.h>
#include <string.h>
#include "cJSON.h"

#define BUF (1 << 16)

/* Parse n bytes of s. Returns the cJSON_GetErrorPtr() offset (>= 0) on parse
 * failure, or -1 if the parse accepted. */
static long parse_errpos(const char *s, size_t n) {
    cJSON *j = cJSON_ParseWithLength(s, n);
    if (j) {
        cJSON_Delete(j);
        return -1;
    }
    const char *ep = cJSON_GetErrorPtr();
    return ep ? (long)(ep - s) : 0;
}

int main(void) {
    static char buf[BUF + 1]; /* +1 headroom for the trailing-space probe */
    size_t n = fread(buf, 1, BUF, stdin);

    long p0 = parse_errpos(buf, n);
    if (p0 < 0) {
        return 0; /* accept: whole buffer is one valid JSON value */
    }
    if (n == 0) {
        return 10; /* empty input ended before any value — wants more */
    }

    /* Differential: one trailing whitespace exposes whether the failure was at
     * end-of-content (alive) or on a present interior byte (dead). */
    buf[n] = ' ';
    long p1 = parse_errpos(buf, n + 1);
    if (p1 < 0 || p1 >= (long)n || p1 > p0) {
        return 10; /* alive: it failed because input ran out */
    }
    return 11; /* dead: committed error on a present byte */
}
