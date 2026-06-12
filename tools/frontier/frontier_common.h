/* frontier_common.h — shared input construction for frontier harnesses.
 *
 * The frontier driver explores inputs as concrete-prefix + symbolic-window:
 *   buf = decode_hex(argv[1]) || win[0:len]
 * where win is FRONTIER_WINDOW symbolic bytes and len is a symbolic length
 * in [0, FRONTIER_WINDOW]. The symbolic len gives KLEE an explicit
 * "input could end here" fork at every window offset — the branch that
 * length-exact parsers (luaL_loadbuffer, yyjson_read) never expose
 * themselves, which is why stock KLEE cannot produce valid inputs for
 * them (see .dev/parser-guided-eval/run8_pivot-synthesis/REPORT.md §5.1).
 *
 * klee_make_symbolic/klee_assume are declared extern rather than via
 * <klee/klee.h> so the harness compiles with the stock per-subject
 * build scripts (no extra include paths); KLEE resolves them at run
 * time as special functions.
 */
#ifndef FRONTIER_COMMON_H
#define FRONTIER_COMMON_H

#include <stddef.h>

extern void klee_make_symbolic(void *addr, size_t nbytes, const char *name);
extern void klee_assume(unsigned long condition);

#ifndef FRONTIER_WINDOW
#define FRONTIER_WINDOW 4
#endif

static int fr_hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

/* Fill buf with prefix||win[0:len]; return total length (< cap). */
static size_t frontier_fill(char *buf, size_t cap, int argc, char **argv) {
    size_t plen = 0;

    if (argc > 1 && !(argv[1][0] == '-' && argv[1][1] == '\0')) {
        const char *h = argv[1];
        size_t i = 0;
        while (h[i] && h[i + 1] && plen < cap) {
            int hi = fr_hexval(h[i]), lo = fr_hexval(h[i + 1]);
            if (hi < 0 || lo < 0) break;
            buf[plen++] = (char)((hi << 4) | lo);
            i += 2;
        }
    }

    unsigned char win[FRONTIER_WINDOW];
    unsigned char wlen;
    klee_make_symbolic(win, sizeof win, "win");
    klee_make_symbolic(&wlen, sizeof wlen, "len");
    klee_assume(wlen <= FRONTIER_WINDOW);

    for (size_t i = 0; i < FRONTIER_WINDOW && plen < cap; i++) {
        if (i >= wlen) break; /* symbolic: forks "ends here" at each offset */
        /* A NUL window byte duplicates the ends-here fork (NUL-terminated
         * subjects) or is lexically invalid (the rest); excluding it kills
         * degenerate \0-padding candidates. Cost: inputs needing a raw NUL
         * mid-stream (e.g. NUL inside a Lua long string) are unreachable —
         * none of the benchmark grammars requires one. */
        klee_assume(win[i] != 0);
        buf[plen++] = (char)win[i];
    }
    return plen;
}

#endif /* FRONTIER_COMMON_H */
