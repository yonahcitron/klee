/* janet_survival.c — native survival classifier for the Lisp-like parser
 * subject janet (Janet's streaming reader, via the PARSER API).
 *
 * Exit code is the true survival signal, read from the parser's control flow:
 *   exit 0  = accept  (JANET_PARSE_ROOT: every byte consumed and the parser
 *                      sits at top level — one or more complete values, ready
 *                      for more top-level input)
 *   exit 10 = alive   (JANET_PARSE_PENDING: input ended mid-construct, e.g. an
 *                      unclosed '(' — the reader's state stack is still deep,
 *                      so it WANTS MORE. This is the key viable-prefix signal,
 *                      and is exactly the ROOT/PENDING branch Janet's own REPL
 *                      uses to decide whether to read another line)
 *   exit 11 = dead    (JANET_PARSE_ERROR: a committed parse error on a present
 *                      byte, e.g. a mismatched ']' closing a '(')
 *
 * Unlike janet_dostring, the streaming parser exposes the PENDING state, so
 * "wants more" is observed directly from the parser status rather than from
 * an error token. We deliberately do NOT call janet_parser_eof() before
 * reading the status: eof would flush a mid-construct PENDING into DEAD/ERROR
 * and so destroy the alive(10) signal. (Status logic, janet.c:
 *   error -> JANET_PARSE_ERROR; flag(eof) -> JANET_PARSE_DEAD;
 *   statecount>1 -> JANET_PARSE_PENDING; else JANET_PARSE_ROOT.)
 *
 * Build: clang/afl-clang-fast -O2 -w janet_survival.c <amalgamated janet.c>
 *   -I<dir with janet.h> -lm -lpthread -ldl -lrt  (see build-run15.sh /
 *   build-frontier.sh; the JANET_NO_* feature flags from the subject build.sh
 *   drop the pthread/dl/rt deps for the KLEE bitcode path).
 */
#include <stdio.h>
#include "janet.h"

#define BUF (1 << 16)

int main(void) {
    static char buf[BUF];
    size_t n = fread(buf, 1, sizeof buf, stdin);

    if (janet_init() != 0) return 2;

    JanetParser p;
    janet_parser_init(&p);

    for (size_t i = 0; i < n; i++) {
        janet_parser_consume(&p, (uint8_t) buf[i]);
        if (janet_parser_status(&p) == JANET_PARSE_ERROR) break;
    }

    int out;
    switch (janet_parser_status(&p)) {
        case JANET_PARSE_ERROR:   out = 11; break;  /* committed parse error */
        case JANET_PARSE_PENDING: out = 10; break;  /* mid-construct: wants more */
        case JANET_PARSE_ROOT:    out = 0;  break;  /* clean top level: complete */
        default:                  out = 11; break;  /* DEAD shouldn't occur (no eof) */
    }

    janet_parser_deinit(&p);
    janet_deinit();
    return out;
}
