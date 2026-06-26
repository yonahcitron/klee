/* tinyc_survival.c — native survival classifier for run13 (pFuzzer-via-KLEE).
 *
 * Exit code reports the program's own control flow:
 *   exit 0  = accept     (program() returned: parse complete, sym==EOI)
 *   exit 10 = "wanted more" — syntax_error AFTER reading past end-of-input
 *   exit 11 = "committed"   — syntax_error with input bytes still present
 *
 * This is a COARSE survival prune, NOT run13's primary signal. tiny.c's lexer
 * reads one char of lookahead, so a committed multi-char error that ends at EOF
 * (`whila`, `elsex`, `while)`) also trips read-past-end and is reported alive
 * (10); only errors with bytes still remaining (`whil@`, `while)x`) are cleanly
 * dead (11). That is acceptable: run13 separates extendable-from-dead by the
 * matched-count from KLEE's Eq pins (the constraint signal, leak #1), NOT by
 * this bit — `whila`/`elsex` freeze at matched 4 while `while` reaches 5 and
 * climbs. The bit's job is only to detect accepts (exact) and prune the obvious
 * committed dead-ends; it deliberately does NOT reintroduce v5/v6's
 * instrumented-strcmp proxy. See DESIGN.md "Honest limitations" §1.
 *
 * Parse-only, like tinyc_native.c: call program() directly, skip codegen/VM.
 * syntax_error() is tiny.c's only exit() call.
 *
 * Tracked source; build-run13.sh copies it next to the machine-local subject
 * sources (tiny.c, tinyc_fr.c) in .local/frontier-builds-tinyc/ to compile.
 * tiny.c is Marc Feeley's tiny-C ("All Rights Reserved"), kept machine-local
 * and not committed.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static unsigned char FR_BUF[1 << 16];
static long FR_LEN = 0, FR_POS = 0;
static int FR_PAST_END = 0;

static int fr_getchar(void) {
    if (FR_POS < FR_LEN) return (int)FR_BUF[FR_POS++];
    FR_PAST_END = 1;        /* parser asked for a byte past the input end */
    return EOF;
}

static void fr_exit(int code) {
    /* tiny.c reaches exit() only via syntax_error() (code 1); accept returns */
    if (code == 0) _exit(0);
    _exit(FR_PAST_END ? 10 : 11);
}

#define getchar() fr_getchar()
#define exit(c)   fr_exit(c)
#define main      tiny_main
#include "tiny.c"
#undef main
#undef exit
#undef getchar

int main(void) {
    FR_LEN = (long)fread(FR_BUF, 1, sizeof FR_BUF, stdin);
    FR_POS = 0;
    program();              /* returns iff accepted; else fr_exit via syntax_error */
    return 0;               /* accept */
}
