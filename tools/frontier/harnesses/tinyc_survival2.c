/* tinyc_survival2.c — ACCURATE survival classifier for run13-v2 (run14).
 *
 * Fixes v1's read-past-end leak, which mislabelled committed multi-char errors
 * as alive (`whila`, `elsex`, `while)`, and crucially the extra-token case
 * `(d)+e-i;w`) because tiny.c's lexer reads one lookahead char that hits EOF.
 * That leak let run13-v1 pour its whole budget into a dead basin and bank 0
 * keyword statements (RESULTS.md).
 *
 * Instead of inferring survival from where a read landed, this reads the
 * parser's actual control flow:
 *   exit 0  accept : the first statement parsed AND nothing follows (sym==EOI)
 *   exit 10 alive  : a viable prefix — either the current identifier is a STRICT
 *                    prefix of a keyword (more letters could complete it: `whil`
 *                    -> `while`), or the parser ran out of tokens mid-production
 *                    wanting more (sym==EOI, e.g. `while` wants `(`)
 *   exit 11 dead   : committed — a multi-char non-keyword identifier (`whil_`,
 *                    `whila`), a wrong token in context (`while)`), an illegal
 *                    char, or extra tokens after a complete statement (`...;w`)
 *
 * The keyword-prefix test is the program's own keyword set (`words[]`), not a
 * hardcoded list — the same data tiny.c compares against. Driving next_sym()+
 * statement() directly (instead of program()) lets us catch the post-complete
 * extra-token case that program()'s single `sym!=EOI` syntax_error hides.
 *
 * Native; reads the candidate from stdin via tiny.c's own getchar(). Tracked;
 * build copies it next to the machine-local tiny.c (see build-run13.sh).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* tiny.c globals (defined when tiny.c is included below). */
extern char id_name[];
extern char *words[];
extern int sym;

#define FR_EOI 15          /* tiny.c enum index of EOI; checked post-include */

/* Is the current identifier a STRICT prefix of some keyword? (non-empty id that
 * matches a keyword's leading chars but is shorter — more letters could finish
 * it). This is exactly "alive, mid-keyword". A committed wrong char (whil_) or a
 * full keyword (while) is not a strict prefix. */
static int fr_kw_prefix(void) {
    if (id_name[0] == '\0') return 0;
    for (int s = 0; words[s]; s++) {
        const char *kw = words[s], *id = id_name;
        int i = 0;
        while (kw[i] && kw[i] == id[i]) i++;
        if (id[i] == '\0' && kw[i] != '\0') return 1;   /* id strict-prefix of kw */
    }
    return 0;
}

static void fr_exit(int code) {
    /* syntax_error() during parsing reaches here (code 1); accept never does */
    if (code == 0) _exit(0);
    _exit((fr_kw_prefix() || sym == FR_EOI) ? 10 : 11);
}

#define exit(c) fr_exit(c)
#define main    tiny_main
#include "tiny.c"
#undef main
#undef exit

_Static_assert(EOI == FR_EOI, "tiny.c token enum changed; update FR_EOI");

int main(void) {
    next_sym();              /* prime the first token, as program() does */
    statement();             /* parse ONE statement; syntax_error -> fr_exit */
    /* statement() returned => the first statement is complete; anything left is
     * an extra token (single-statement grammar), which is dead. */
    _exit(sym == EOI ? 0 : 11);
}
