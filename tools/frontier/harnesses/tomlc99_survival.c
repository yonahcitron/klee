/* tomlc99_survival.c — native survival classifier for the tomlc99 TOML
 * subject (toml_parse, length-exact: the whole NUL-terminated buffer is
 * the document; there is no getchar / read-past-end).
 *
 * Exit code is the true survival signal, read from the parser's control flow:
 *   exit 0  = accept  (toml_parse returned a table: the whole input is valid
 *                      TOML — criterion-1 validity)
 *   exit 10 = alive   (parse failed because the input ENDED while more was
 *                      expected — an unterminated string/quote/escape that ran
 *                      into end-of-buffer; a viable prefix a longer suffix
 *                      could complete)
 *   exit 11 = dead    (parse failed on a PRESENT byte — a committed error a
 *                      longer suffix cannot fix: bad key, missing '=', a
 *                      non-hex byte inside an escape, extra chars, etc.)
 *
 * tomlc99 is length-exact like luac/yyjson: it parses the whole buffer rather
 * than reading past the end, so "wants more" is observed from the error
 * MESSAGE (errbuf), not from a short read. toml_parse writes a human string
 * into errbuf; the EOF-class messages are the lexer's "ran off the end of a
 * string/escape while still open" reports. The observed errbuf vocabulary
 * (tomlc99 vendored copy) is:
 *
 *   alive (ended early):  "line N: unterminated quote"
 *                         "line N: unterminated s-quote"
 *                         "line N: unterminated triple-s-quote"
 *                         "line N: unterminated triple-d-quote"
 *                         "line N: expect an escape char"   (\ then EOF)
 *                         "line N: expected more hex char"  (\u/\U then EOF)
 *   dead (present byte):  "line N: syntax error", "line N: bad key",
 *                         "line N: invalid key", "line N: missing =",
 *                         "line N: expect hex char" (a *present* non-hex byte),
 *                         "line N: extra chars after value", "key exists",
 *                         "invalid char U+xxxx", "internal error (...)", ...
 *
 * The classifier lowercases errbuf and tests for any EOF-class substring. The
 * trigger list is tuned against the live parser (see the survival.c report):
 * "unterminated" and "expected more hex" / "expect an escape" are the clean
 * EOF markers. We deliberately do NOT match a bare "expect", because
 * "expect hex char" fires on a present non-hex byte (a committed error) — a
 * bare "expect" would misclassify that dead case as alive. The fuller list
 * ("unexpected end", "end of", "eof", "newline") is kept for robustness /
 * grammar drift; none of those strings are emitted by this vendored tomlc99,
 * so they are harmless future-proofing.
 *
 * toml_parse MUTATES its buffer, so we keep a writable, NUL-terminated copy.
 *
 * Native; reads the candidate from stdin. Built with afl-clang-fast so
 * afl-showmap gives the coverage-novelty signal for --unified (see the
 * harness_survival_afl build in build-frontier / the survival.c report).
 */
#include <ctype.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "toml.h"

#define BUF (1 << 16)

/* EOF-class substrings: errbuf containing any of these means the input ended
 * while the lexer still wanted more (a viable prefix → alive). Tuned against
 * the live tomlc99 parser; see header comment for why bare "expect" is absent. */
static const char *const EOF_MARKERS[] = {
    "unterminated",     /* unterminated [s-|triple-s-|triple-d-]quote */
    "expect an escape", /* '\' as the last byte before EOF            */
    "expected more hex", /* '\u'/'\U' truncated by EOF                */
    "expected",         /* generic "expected ..." (grammar drift)     */
    "unexpected end",   /* robustness: not emitted by this tomlc99    */
    "end of",
    "eof",
    "newline",
};

int main(void) {
    static char buf[BUF + 1];
    ssize_t total = 0, n;
    while (total < BUF &&
           (n = read(0, buf + total, BUF - total)) > 0) {
        total += n;
    }
    buf[total] = '\0';

    char errbuf[256] = {0};
    toml_table_t *t = toml_parse(buf, errbuf, sizeof errbuf);
    if (t) {
        toml_free(t);
        return 0;
    }

    /* lowercase errbuf in place for case-insensitive substring matching */
    for (char *q = errbuf; *q; q++)
        *q = (char)tolower((unsigned char)*q);

    for (size_t i = 0; i < sizeof EOF_MARKERS / sizeof EOF_MARKERS[0]; i++) {
        if (strstr(errbuf, EOF_MARKERS[i]))
            return 10; /* alive: ended early, viable prefix */
    }
    return 11; /* dead: committed error on a present byte */
}
