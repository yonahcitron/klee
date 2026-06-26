/* inih_survival.c — survival classifier for inih (line-oriented INI parser).
 *
 * Exit code reports the parser's own control flow, read from ini_parse_string's
 * return value (0 = ok, else the LINE NUMBER of the first error, <0 = OOM):
 *   exit 0  accept : ini_parse_string == 0 (every line parsed cleanly — the
 *                    whole buffer is valid INI)
 *   exit 10 alive  : the first error is on the LAST line of input AND that line
 *                    is an unterminated section — it starts with '[' (after
 *                    leading whitespace) but contains no ']'. A longer suffix
 *                    could append the ']' and close it, so it is a viable prefix.
 *   exit 11 dead   : any other parse error (a committed error — a missing '='
 *                    on a key line, a ']'-less section that is NOT the last
 *                    line, or rc<0) that a longer suffix cannot repair.
 *
 * HONESTY NOTE — the alive(10) signal is WEAK for inih, by construction.
 * inih is line-oriented and permissive: ini_parse_stream() consumes the buffer
 * one '\n'-delimited line at a time and classifies each line independently
 * (comment / [section] / name=value / continuation). There is no scanner
 * lookahead and almost no cross-line "wants more" state. The *only* construct
 * an appended suffix can rescue is an unterminated trailing section "[sec" —
 * append "]" and it becomes "[sec]". Every other error (notably a line with no
 * '=' / ':', which inih rejects because INI_ALLOW_NO_VALUE defaults to 0) is
 * committed the instant the offending byte is present: no suffix helps, because
 * the next '\n' ends the line and the error is already latched (error=lineno).
 * So alive fires for essentially one shape only and never for the common errors.
 * This is the documented, expected outcome — not a bug. Compare luac/yyjson,
 * whose grammars have a real "<eof>/UNEXPECTED_END wants more" state; inih does
 * not, so this bit is mostly an accept/dead discriminator with a thin alive lip.
 *
 * Pure parse with a no-op handler — no execution, no infinite-loop hazard.
 * Reads the whole candidate from stdin into a NUL-terminated buffer (the buffer
 * IS the document: ini_parse_string takes a zero-terminated string).
 *
 * Native; built with afl-clang-fast so afl-showmap yields the coverage-novelty
 * signal, and as window-2 KLEE bitcode via build-frontier.sh (see the inih
 * .local/frontier-builds-modern build).
 */
#include <stdio.h>
#include <string.h>
#include <ctype.h>

#include "ini.h"

#define BUF (1 << 16)

static int sink(void *u, const char *s, const char *n, const char *v) {
    (void)u; (void)s; (void)n; (void)v;
    return 1; /* keep parsing */
}

/* Is the 1-based line number `lineno` the last line of `buf`, AND is that line
 * an unterminated section: first non-whitespace char is '[' but the line holds
 * no ']'? Returns 1 if so (the alive case), 0 otherwise. Scans the buffer the
 * same way ini_reader_string does: lines are '\n'-delimited. "Last line" means
 * no '\n' after the line's content (a trailing-newline-terminated line is NOT
 * a viable prefix — its construct is already closed off by the newline). */
static int alive_unterminated_section(const char *buf, int lineno) {
    const char *p = buf;
    int cur = 1;

    /* Walk to the start of line `lineno`. */
    while (cur < lineno && *p) {
        if (*p == '\n')
            cur++;
        p++;
    }
    if (cur != lineno || *p == '\0')
        return 0;  /* ran off the end before reaching the error line */

    /* p now points at the first byte of the error line. Find its end. */
    const char *line_start = p;
    const char *line_end = p;
    while (*line_end && *line_end != '\n')
        line_end++;

    /* Must be the LAST line: nothing (no '\n') after it. */
    if (*line_end == '\n')
        return 0;

    /* First non-whitespace char must be '[' (matches ini_lskip / ini_rstrip:
     * leading whitespace is skipped before the '[' test in ini_parse_stream). */
    const char *s = line_start;
    while (s < line_end && isspace((unsigned char)*s))
        s++;
    if (s >= line_end || *s != '[')
        return 0;

    /* And the line must contain no ']' — i.e. the section is unterminated. */
    if (memchr(line_start, ']', (size_t)(line_end - line_start)) != NULL)
        return 0;

    return 1;
}

int main(void) {
    static char buf[BUF + 1];
    size_t n = fread(buf, 1, BUF, stdin);
    buf[n] = '\0';  /* ini_parse_string needs a NUL-terminated string */

    int rc = ini_parse_string(buf, sink, NULL);

    int out;
    if (rc == 0) {
        out = 0;                                   /* accept */
    } else if (rc > 0 && alive_unterminated_section(buf, rc)) {
        out = 10;                                  /* alive: viable prefix */
    } else {
        out = 11;                                  /* dead: committed error */
    }
    return out;
}
