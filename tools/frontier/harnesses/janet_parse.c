/* janet PARSE-ONLY frontier harness — prefix||window driven through Janet's
 * streaming PARSER API (janet_parser_*), NOT janet_dostring.
 *
 * The executing variant (harnesses/janet.c) calls janet_dostring, which both
 * parses AND evaluates the input. Under KLEE that evaluation path reaches an
 * undefined _setjmp (Janet's signal/longjmp machinery), erroring the state so
 * the driver banks 0 valids. This harness drives only the parser over the
 * symbolic bytes — exactly as harnesses/janet_survival.c does for the native
 * survival classifier — so KLEE reads the parser's byte comparisons without
 * ever entering the evaluator.
 *
 * Input layout is identical to janet.c: frontier_fill() builds
 * prefix||symbolic-window into buf; we feed those bytes to the parser one at a
 * time and stop on a committed parse error. The return code is irrelevant to
 * KLEE (it does not gate the constraint search); the point is that the parser
 * consumes the symbolic bytes, forking on each comparison.
 */
#include "frontier_common.h"

#include "janet.h"

#define HARNESS_BUF 4096

int main(int argc, char **argv) {
    static char buf[HARNESS_BUF + 1];
    size_t total = frontier_fill(buf, HARNESS_BUF, argc, argv);
    buf[total] = '\0';

    if (janet_init() != 0) return 2;

    JanetParser p;
    janet_parser_init(&p);

    for (size_t i = 0; i < total; i++) {
        janet_parser_consume(&p, (uint8_t) buf[i]);
        if (janet_parser_status(&p) == JANET_PARSE_ERROR) break;
    }

    int status = janet_parser_status(&p);

    janet_parser_deinit(&p);
    janet_deinit();

    /* 0 = parser at clean top level (ROOT); 1 = otherwise (PENDING/ERROR).
     * KLEE ignores this; it is only meaningful for a native run. */
    return status == JANET_PARSE_ROOT ? 0 : 1;
}
